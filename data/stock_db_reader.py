import sqlite3
import pandas as pd
import os

def view_market_data():
    # 에러 로그에 남은 실제 경로를 반영하여 절대 경로로 지정했습니다.
    # 만약 db 파일들이 다른 곳에 있다면 이 경로를 수정해 주세요.
    db_path = r"c:\Users\Kyungsoo\Documents\Code\Investment\data\stocks.db"

    print(f"[{db_path}] 파일 위치를 확인합니다...")

    # 1. 파일 존재 여부 검사
    if not os.path.exists(db_path):
        print("❌ 오류: 해당 경로에 파일이 없습니다. 경로를 다시 확인해 주세요.")
        return
    
    if os.path.getsize(db_path) == 0:
        print("❌ 오류: 파일 용량이 0바이트입니다. 잘못된 빈 파일이 생성되었습니다.")
        return

    print("✅ 파일을 찾았습니다. 데이터베이스를 읽어옵니다...\n")
    
    # conn 변수 초기화 (UnboundLocalError 방지)
    conn = None 
    
    # 2. SQLite DB 연결
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 테이블 목록 조회
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        
        if not tables:
            print("데이터베이스에 테이블이 존재하지 않습니다. (-wal 파일, -shm 파일이 같은 폴더에 있는지 확인하세요.)")
            return

        # 3. 데이터 읽어오기
        for table in tables:
            table_name = table[0]
            print(f"=== 테이블 명: {table_name} ===")
            
            # pandas로 데이터 읽기
            df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
            
            print(f"총 {len(df):,}건의 주식 데이터가 있습니다.\n")
            print("[상위 5개 종목 데이터 미리보기]")
            print(df.head().to_string())
            print("-" * 50)
            
            # 원하신다면 아래 주석(#)을 풀어서 데이터를 CSV 엑셀 파일로 바로 저장할 수도 있습니다.
            # 현재 실행 중인 파이썬 스크립트(.py)가 위치한 폴더의 절대 경로를 가져옵니다.
            current_dir = os.path.dirname(os.path.abspath(__file__))

            # 해당 폴더 경로와 저장할 파일 이름을 합칩니다.
            save_path = os.path.join(current_dir, f"{table_name}_extracted.csv")

            # 지정된 절대 경로에 저장합니다.
            df.to_csv(save_path, index=False, encoding='utf-8-sig')
            print(f"✅ [{save_path}]에 파일이 저장되었습니다.")
            
    except sqlite3.Error as e:
        print(f"❌ 데이터베이스 읽기 오류 발생: {e}")
    finally:
        # 안전하게 DB 연결 종료
        if conn:
            conn.close()

if __name__ == "__main__":
    view_market_data()