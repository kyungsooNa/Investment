import os
import sys

def search_keyword_in_logs(directory_path, keyword):
    """
    지정된 디렉토리 내의 모든 .log 파일에서 특정 키워드를 검색하고 진행률을 표시합니다.
    """
    log_files = []
    for root, _, files in os.walk(directory_path):
        for file in files:
            if file.endswith('.log'):
                log_files.append(os.path.join(root, file))
                
    total_files = len(log_files)
    results = []

    if total_files == 0:
        print("⚠️ 탐색할 .log 파일을 찾을 수 없습니다.")
        return results

    print(f"총 {total_files}개의 로그 파일을 찾았습니다. 탐색을 시작합니다...\n")

    for index, file_path in enumerate(log_files, 1):
        display_name = os.path.basename(file_path)
        progress_msg = f"\r🔄 진행률: [{index}/{total_files}] 탐색 중... 📄 {display_name}"
        
        sys.stdout.write(progress_msg.ljust(80))
        sys.stdout.flush()

        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line_number, line in enumerate(f, 1):
                    if keyword in line:
                        results.append({
                            'file': file_path,
                            'line_number': line_number,
                            'content': line.strip()
                        })
        except Exception as e:
            print(f"\n⚠️ [{file_path}] 읽기 오류: {e}")

    # 탐색 완료 후 진행률 표시줄 지우기
    sys.stdout.write("\r" + " " * 80 + "\r")
    sys.stdout.flush()

    return results

if __name__ == "__main__":
    # ==========================================
    # 💡 사용자 설정 부분
    # ==========================================
    # 실행 위치(터미널)와 상관없이 '현재 파이썬 파일이 있는 폴더'의 절대 경로를 가져옵니다.
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    
    TARGET_DIR = SCRIPT_DIR                 # 탐색할 폴더 경로 (기본값: 스크립트가 위치한 폴더)
    SEARCH_KEYWORD = "ERROR"                # 찾고자 하는 키워드
    OUTPUT_FILENAME = "search_results.txt"  # 생성할 파일 이름 (경로 제외)
    # ==========================================

    # 저장할 파일의 전체 경로 생성 (TARGET_DIR + OUTPUT_FILENAME)
    output_path = os.path.join(TARGET_DIR, OUTPUT_FILENAME)

    print("-" * 60)
    print(f"🔍 '{TARGET_DIR}' 경로에서 '{SEARCH_KEYWORD}' 키워드를 탐색합니다.")
    print("-" * 60)

    found_data = search_keyword_in_logs(TARGET_DIR, SEARCH_KEYWORD)

    # 결과를 지정된 대상 폴더에 텍스트 파일로 저장
    if found_data:
        try:
            with open(output_path, 'w', encoding='utf-8') as f_out:
                f_out.write(f"🔍 검색 키워드: '{SEARCH_KEYWORD}'\n")
                f_out.write(f"📁 탐색 경로: '{TARGET_DIR}'\n")
                f_out.write(f"✅ 총 매칭 건수: {len(found_data)}건\n")
                f_out.write("=" * 60 + "\n\n")

                for item in found_data:
                    f_out.write(f"📄 파일: {item['file']}\n")
                    f_out.write(f"🔢 라인: {item['line_number']}번째 줄\n")
                    f_out.write(f"💬 내용: {item['content']}\n")
                    f_out.write("-" * 60 + "\n")
            
            print("✨ 탐색이 완료되었습니다.")
            print(f"✅ 총 {len(found_data)}개의 매칭 결과를 찾았습니다.")
            print(f"📁 상세 결과가 '{output_path}' 경로에 안전하게 저장되었습니다!")
        except Exception as e:
            print(f"⚠️ 결과 파일 저장 중 오류가 발생했습니다: {e}")
    else:
        print("✅ 탐색이 완료되었습니다. 해당 키워드가 포함된 로그를 찾지 못했습니다.")