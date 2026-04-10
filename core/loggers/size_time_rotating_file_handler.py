import os
import glob
from logging.handlers import RotatingFileHandler

class SizeTimeRotatingFileHandler(RotatingFileHandler):
    """
    파일 크기가 maxBytes를 초과하면 인덱스를 붙여 새 파일로 교체하는 핸들러.
    인덱스가 클수록 최신 파일입니다.
    예: app_1.log (가장 오래됨) ... app_25.log (오래된 백업) -> app_26.log (현재 활성)
    """
    def __init__(self, filename, mode='a', maxBytes=0, backupCount=0, encoding=None, delay=False):
        # 확장자 처리 (예: .log.json)
        if filename.endswith(".log.json"):
            root, ext = filename[:-len(".log.json")], ".log.json"
        else:
            root, ext = os.path.splitext(filename)

        self._log_root = root
        self._log_ext = ext
        self._current_index = self._find_max_index()

        initial_filename = f"{self._log_root}_{self._current_index + 1}{self._log_ext}"
        super().__init__(initial_filename, mode=mode, maxBytes=maxBytes,
                         backupCount=backupCount, encoding=encoding, delay=delay)

    def _find_max_index(self):
        pattern = f"{glob.escape(self._log_root)}_[0-9]*{glob.escape(self._log_ext)}"
        max_index = 0
        for f in glob.glob(pattern):
            try:
                idx_str = f[:-len(self._log_ext)].split('_')[-1]
                if idx_str.isdigit():
                    max_index = max(max_index, int(idx_str))
            except (ValueError, IndexError):
                continue
        return max_index

    def doRollover(self):
        if self.stream:
            self.stream.close()
            self.stream = None

        # glob 스캔 제거: 메모리 인덱스만 +1 증가
        self._current_index += 1
        next_filename = f"{self._log_root}_{self._current_index + 1}{self._log_ext}"
        self.baseFilename = os.path.abspath(next_filename)

        # 오래된 파일 삭제 (backupCount 초과 시)
        if self.backupCount > 0:
            pattern = f"{glob.escape(self._log_root)}_[0-9]*{glob.escape(self._log_ext)}"
            all_files = glob.glob(pattern)
            all_files.sort(key=lambda f: int(f[:-len(self._log_ext)].split('_')[-1])
                           if f[:-len(self._log_ext)].split('_')[-1].isdigit() else -1)
            if len(all_files) > self.backupCount:
                for f in all_files[:len(all_files) - self.backupCount]:
                    try:
                        os.remove(f)
                    except OSError:
                        pass

        if not self.delay:
            self.stream = self._open()
