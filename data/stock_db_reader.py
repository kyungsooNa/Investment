import sqlite3
import pandas as pd
import os

def view_market_data():
    # 에러 로그에 남은 실제 경로를 반영하여 절대 경로로 지정했습니다.
    # 만약 db 파일들이 다른 곳에 있다면 이 경로를 수정해 주세요.
    db_path = os.path.join(os.path.dirname(__file__), "stocks.db")

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
            
            # 현재 실행 중인 파이썬 스크립트(.py)가 위치한 폴더의 절대 경로를 가져옵니다.
            current_dir = os.path.dirname(os.path.abspath(__file__))

            # 해당 폴더 경로와 저장할 파일 이름을 합칩니다.
            save_path = os.path.join(current_dir, f"{table_name}_extracted.csv")
            summary_save_path = os.path.join(current_dir, f"{table_name}_db_summary.txt")

            # --- 종목별 데이터 수 집계 (Summary) ---
            if 'code' in df.columns:
                summary_df = df.groupby('code').size().reset_index(name='data_count')
                total_codes = len(summary_df)
                avg_days = summary_df['data_count'].mean()
                
                summary_lines = [
                    f"=== [{table_name} 데이터 요약] ===",
                    f"- 총 종목 수: {total_codes:,}개",
                    f"- 평균 저장 일수: {avg_days:.1f}일",
                ]
                
                # ohlcv 테이블인 경우 600일 미만 종목 추출
                if table_name == 'ohlcv':
                    under_600_df = summary_df[summary_df['data_count'] < 600]
                    under_600_count = len(under_600_df)
                    summary_lines.append(f"- 600일 미만 데이터 보유 종목 수: {under_600_count:,}개")
                    
                    if under_600_count > 0:
                        summary_lines.append("\n[600일 미만 종목 리스트]")
                        under_600_list = under_600_df.to_dict('records')
                        chunk_size_under = 10
                        for i in range(0, under_600_count, chunk_size_under):
                            chunk = under_600_list[i:i+chunk_size_under]
                            chunk_str = ", ".join([f"{item['code']} ({item['data_count']}일)" for item in chunk])
                            summary_lines.append(f"  {chunk_str}")

                summary_lines.append("\n[전체 종목별 저장 일수]")
                # 가독성을 위해 15개 종목씩 묶어서 한 줄에 표기
                chunk_size = 15
                for i in range(0, total_codes, chunk_size):
                    chunk = summary_df.iloc[i:i+chunk_size]
                    chunk_str = ", ".join([f"{row['code']} ({row['data_count']}일)" for _, row in chunk.iterrows()])
                    summary_lines.append(f"  {chunk_str}")
                
                summary_lines.append(f"{'=' * 50}\n")
                summary_text = "\n".join(summary_lines)

                # 요약 정보는 별도의 텍스트 파일로 저장
                with open(summary_save_path, 'w', encoding='utf-8-sig') as f:
                    f.write(summary_text)
                
                print(f"✅ 요약 정보가 별도 파일로 분리되었습니다: [{summary_save_path}]")
            
            # 순수 원본 데이터만 CSV에 저장
            df.to_csv(save_path, index=False, encoding='utf-8-sig')
            print(f"✅ 데이터가 저장되었습니다 (순수 데이터): [{save_path}]")
            
    except sqlite3.Error as e:
        print(f"❌ 데이터베이스 읽기 오류 발생: {e}")
    finally:
        # 안전하게 DB 연결 종료
        if conn:
            conn.close()

if __name__ == "__main__":
    view_market_data()