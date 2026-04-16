import gzip
import shutil
import os
from pathlib import Path

def decompress_by_timestamp(input_file_path):
    # 1. 입력 파일 경로 객체 생성 및 검증
    target_path = Path(input_file_path)
    if not target_path.exists():
        print(f"오류: '{input_file_path}' 파일을 찾을 수 없습니다.")
        return

    # 2. 파일명에서 타임스탬프 추출 (예: 20260416_214848)
    # 파일명의 첫 15자리가 YYYYMMDD_HHMMSS 형식임을 가정합니다.
    timestamp = target_path.name[:15]
    directory = target_path.parent
    
    print(f"기준 타임스탬프: {timestamp}")
    print(f"탐색 디렉토리: {directory.absolute()}")

    # 3. 해당 디렉토리에서 동일한 타임스탬프로 시작하는 .gz 파일 찾기
    gz_files = [f for f in directory.glob(f"{timestamp}*.gz")]

    if not gz_files:
        print(f"해당 타임스탬프({timestamp})와 일치하는 .gz 파일이 없습니다.")
        return

    print(f"총 {len(gz_files)}개의 연관 압축 파일을 발견했습니다.")

    # 4. 압축 해제 진행
    for gz_file in gz_files:
        # 확장자 제거 (파일명.log.json.gz -> 파일명.log.json)
        output_file = gz_file.with_suffix("")
        
        try:
            print(f"압축 해제 중: {gz_file.name} -> {output_file.name}...", end=" ", flush=True)
            
            with gzip.open(gz_file, 'rb') as f_in:
                with open(output_file, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
            
            print("성공!")
        except Exception as e:
            print(f"실패 (오류: {e})")

if __name__ == "__main__":
    # 사용 예시: 실제 파일명 하나를 입력합니다. (.gz가 붙어있어도, 없어도 타임스탬프만 추출합니다.)
    user_input = input("압축을 풀 타임스탬프의 기준 파일명을 입력하세요: ").strip()
    decompress_by_timestamp(user_input)