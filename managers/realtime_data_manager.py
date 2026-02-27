import asyncio
import json
import os
import logging
from datetime import datetime, timedelta

class RealtimeDataManager:
    """
    실시간 데이터의 수신, 저장, 파일 관리, 클라이언트 전송을 담당하는 매니저.
    """
    def __init__(self, logger=None):
        self.logger = logger if logger else logging.getLogger(__name__)
        
        # 데이터 관리
        self._pt_history: dict = {}  # {code: [data1, data2, ...]}
        self._pt_history_buffer: list = []  # 파일 저장 대기 버퍼
        
        # 클라이언트 스트리밍
        self._pt_queues: list = []  # 접속한 클라이언트들의 큐 리스트
        self._pt_codes: set = set() # 현재 구독 중인 종목 코드 집합
        
        # 백그라운드 태스크
        self._flush_task = None
        
        # 초기화
        self._load_pt_history()

    # --- 파일 경로 및 I/O ---
    def _get_base_dir(self):
        return "data/program_subscribe"

    def _get_pt_history_file_path(self):
        """오늘 날짜 기준 히스토리 파일 경로 반환."""
        today = datetime.now().strftime("%Y%m%d")
        return f"{self._get_base_dir()}/pt_history_{today}.jsonl"

    def _get_snapshot_file_path(self):
        return f"{self._get_base_dir()}/pt_data.json"

    def _load_pt_history(self):
        """파일에서 금일 프로그램 매매 이력을 로드."""
        file_path = self._get_pt_history_file_path()
        if not os.path.exists(file_path):
            return

        count = 0
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        data = json.loads(line)
                        code = data.get('유가증권단축종목코드')
                        if code:
                            self._pt_history.setdefault(code, []).append(data)
                            count += 1
                    except json.JSONDecodeError:
                        continue
            if count > 0:
                self.logger.info(f"기존 히스토리 파일에서 {count}건의 데이터를 복구했습니다.")
        except Exception as e:
            self.logger.error(f"히스토리 로드 중 오류: {e}")

    def _flush_pt_history(self):
        """버퍼에 있는 데이터를 파일에 저장하고 비움."""
        if not self._pt_history_buffer:
            return

        buffer_to_write = self._pt_history_buffer
        self._pt_history_buffer = []

        try:
            os.makedirs(self._get_base_dir(), exist_ok=True)
            file_path = self._get_pt_history_file_path()
            with open(file_path, "a", encoding="utf-8") as f:
                for item in buffer_to_write:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
        except Exception as e:
            self.logger.error(f"히스토리 Flush 중 오류: {e}")

    async def _periodic_flush_loop(self):
        """15분마다 데이터를 파일에 Flush."""
        try:
            while True:
                await asyncio.sleep(900)  # 15분
                self._flush_pt_history()
        except asyncio.CancelledError:
            pass

    def _cleanup_old_pt_history(self, retention_days=30):
        """오래된 프로그램 매매 히스토리 파일 삭제."""
        try:
            dir_path = self._get_base_dir()
            if not os.path.exists(dir_path):
                return

            cutoff_date = datetime.now() - timedelta(days=retention_days)
            cutoff_str = cutoff_date.strftime("%Y%m%d")

            for filename in os.listdir(dir_path):
                if filename.startswith("pt_history_") and filename.endswith(".jsonl"):
                    try:
                        if len(filename) >= 19:
                            date_part = filename[11:19]
                            if date_part.isdigit() and date_part < cutoff_str:
                                file_path = os.path.join(dir_path, filename)
                                os.remove(file_path)
                                self.logger.info(f"오래된 히스토리 파일 삭제: {filename}")
                    except Exception as e:
                        self.logger.error(f"파일 삭제 실패 ({filename}): {e}")
        except Exception as e:
            self.logger.error(f"히스토리 정리 중 오류: {e}")

    # --- 데이터 처리 ---
    def on_data_received(self, data: dict):
        """웹소켓 등에서 수신한 데이터를 처리 (저장 및 브로드캐스트)."""
        code = data.get('유가증권단축종목코드')
        if not code:
            return

        # 1. 메모리 및 버퍼 저장
        self._pt_history.setdefault(code, []).append(data)
        self._pt_history_buffer.append(data)

        # 2. 클라이언트 브로드캐스트
        for q in list(self._pt_queues):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(data)
                except Exception:
                    pass
            except Exception:
                pass

    # --- 클라이언트 큐 관리 ---
    def create_subscriber_queue(self) -> asyncio.Queue:
        """새로운 구독자(웹 클라이언트)를 위한 큐 생성 및 등록."""
        queue = asyncio.Queue(maxsize=200)
        self._pt_queues.append(queue)
        return queue

    def remove_subscriber_queue(self, queue: asyncio.Queue):
        """구독자 큐 제거."""
        if queue in self._pt_queues:
            self._pt_queues.remove(queue)

    def get_history_data(self):
        """현재 메모리에 있는 히스토리 데이터 반환."""
        return self._pt_history

    # --- 구독 상태 관리 ---
    def add_subscribed_code(self, code: str):
        self._pt_codes.add(code)

    def remove_subscribed_code(self, code: str):
        self._pt_codes.discard(code)
    
    def clear_subscribed_codes(self):
        self._pt_codes.clear()

    def is_subscribed(self, code: str) -> bool:
        return code in self._pt_codes

    def get_subscribed_codes(self) -> list:
        return sorted(list(self._pt_codes))

    # --- 스냅샷 저장/로드 (수동) ---
    def save_snapshot(self, data_dict: dict):
        try:
            os.makedirs(self._get_base_dir(), exist_ok=True)
            with open(self._get_snapshot_file_path(), "w", encoding="utf-8") as f:
                json.dump(data_dict, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            self.logger.error(f"스냅샷 저장 실패: {e}")
            raise e

    def load_snapshot(self) -> dict:
        file_path = self._get_snapshot_file_path()
        if not os.path.exists(file_path):
            return None
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    # --- 생명주기 관리 ---
    def start_background_tasks(self):
        """백그라운드 태스크 시작."""
        self._cleanup_old_pt_history()
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._periodic_flush_loop())
            self.logger.info("RealtimeDataManager: 데이터 Flush 태스크 시작 (주기: 15분)")

    async def shutdown(self):
        """서비스 종료 처리."""
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None
        
        self._flush_pt_history()
        self.logger.info("RealtimeDataManager: 종료 및 데이터 Flush 완료")
