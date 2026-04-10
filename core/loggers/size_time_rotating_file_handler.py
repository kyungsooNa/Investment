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

        # 기존 인덱스 파일 중 최대 인덱스 탐색
        pattern = f"{glob.escape(root)}_[0-9]*{glob.escape(ext)}"
        max_index = 0
        for f in glob.glob(pattern):
            try:
                idx_str = f[:-len(ext)].split('_')[-1]
                if idx_str.isdigit():
                    max_index = max(max_index, int(idx_str))
            except (ValueError, IndexError):
                continue

        # 초기 활성 파일은 max_index + 1 번 인덱스로 생성
        initial_filename = f"{root}_{max_index + 1}{ext}"
        super().__init__(initial_filename, mode=mode, maxBytes=maxBytes,
                         backupCount=backupCount, encoding=encoding, delay=delay)

    def doRollover(self):
        if self.stream:
            self.stream.close()
            self.stream = None

        # 현재 존재하는 인덱스 파일 중 최대 인덱스 결정
        pattern = f"{glob.escape(self._log_root)}_[0-9]*{glob.escape(self._log_ext)}"
        existing = glob.glob(pattern)

        max_index = 0
        for f in existing:
            try:
                idx_str = f[:-len(self._log_ext)].split('_')[-1]
                if idx_str.isdigit():
                    max_index = max(max_index, int(idx_str))
            except (ValueError, IndexError):
                continue

        # baseFilename을 다음 인덱스 파일로 업데이트 (이것이 새 활성 파일이 됨)
        next_filename = f"{self._log_root}_{max_index + 1}{self._log_ext}"
        self.baseFilename = os.path.abspath(next_filename)

        # 오래된 파일 삭제 (backupCount 초과 시)
        if self.backupCount > 0:
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
