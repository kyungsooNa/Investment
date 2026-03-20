import sqlite3
import pandas as pd
import os

def view_program_trading_data():
    # 현재 실행 중인 파이썬 스크립트(.py)가 위치한 폴더의 절대 경로를 가져옵니다.
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    # DB 파일 경로 설정 (스크립트와 같은 폴더에 있다고 가정)
    db_path = os.path.join(current_dir, 'program_trading.db')
    
    print(f"[{db_path}] 파일 위치를 확인합니다...")

    # 1. 파일 존재 여부 검사
    if not os.path.exists(db_path):
        print("❌ 오류: 해당 경로에 파일이 없습니다. 스크립트와 동일한 폴더에 'program_trading.db'를 넣어주세요.")
        return
    
    if os.path.getsize(db_path) == 0:
        print("❌ 오류: 파일 용량이 0바이트입니다. 잘못된 빈 파일일 수 있습니다.")
        return

    print("✅ 파일을 찾았습니다. 데이터베이스를 읽어옵니다...\n")
    
    conn = None 
    
    # 2. SQLite DB 연결 및 데이터 조회
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 테이블 목록 조회
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        
        if not tables:
            print("데이터베이스에 테이블이 존재하지 않습니다. (-wal, -shm 파일이 함께 있는지 확인해 주세요.)")
            return

        # 3. 데이터 읽어오기 및 CSV 자동 저장
        for table in tables:
            table_name = table[0]
            print(f"=== 테이블 명: {table_name} ===")
            
            # pandas로 데이터 읽기
            df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
            
            print(f"총 {len(df):,}건의 프로그램 매매 데이터가 있습니다.\n")
            print("[상위 5개 데이터 미리보기]")
            print(df.head().to_string())
            print("-" * 50)
            
            # 스크립트가 위치한 동일한 폴더에 CSV 파일로 명확하게 저장
            save_path = os.path.join(current_dir, f"{table_name}_extracted.csv")
            df.to_csv(save_path, index=False, encoding='utf-8-sig')
            
            print(f"✅ 엑셀에서 열어볼 수 있는 CSV 파일이 아래 경로에 생성되었습니다:")
            print(f"   ▶ {save_path}\n")
            
    except sqlite3.Error as e:
        print(f"❌ 데이터베이스 읽기 오류 발생: {e}")
    finally:
        # 안전하게 DB 연결 종료
        if conn:
            conn.close()

if __name__ == "__main__":
    view_program_trading_data()