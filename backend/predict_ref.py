from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Dict, Optional
import torch
import torch.nn as nn
import pandas as pd
import numpy as np
import FinanceDataReader as fdr
from datetime import datetime, timedelta
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_squared_error, mean_absolute_error
from torch.utils.data import DataLoader, Dataset
import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
import time
import yfinance as yf
from functools import lru_cache
import warnings
warnings.filterwarnings('ignore')

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="Korean Stock Prediction Model")

# 전역 변수
model_cache = {}
executor = ThreadPoolExecutor(max_workers=4)
stock_list_cache = None
stock_list_cache_time = None
data_cache = {}
data_cache_time = {}

# Rate Limiter 클래스 
class RateLimiter:
    def __init__(self, max_calls=10, period=5):
        self.max_calls = max_calls
        self.period = period
        self.calls = []
    
    # 비동기 Rate Limit 대기
    async def wait_async(self):
        now = time.time()
        self.calls = [call for call in self.calls if now - call < self.period]
        
        if len(self.calls) >= self.max_calls:
            sleep_time = self.period - (now - self.calls[0]) + 0.1
            if sleep_time > 0 and sleep_time < 5:  # 최대 5초만 대기
                logger.info(f"Rate limit 대기: {sleep_time:.1f}초")
                await asyncio.sleep(sleep_time)
                self.calls = []
        
        self.calls.append(now)
    
    # Rate Limit 대기
    def wait(self):
        now = time.time()
        self.calls = [call for call in self.calls if now - call < self.period]
        
        if len(self.calls) >= self.max_calls:
            sleep_time = min(2.0, self.period - (now - self.calls[0]) + 0.1)
            if sleep_time > 0:
                logger.info(f"Rate limit 대기: {sleep_time:.1f}초")
                time.sleep(sleep_time)
                self.calls = [call for call in self.calls if now - call < self.period/2]
        
        self.calls.append(now)

# Rate Limiter 설정 
fdr_limiter = RateLimiter(max_calls=10, period=5) # 5초에 10번
yf_limiter = RateLimiter(max_calls=30, period=10) # 10초에 30번

# 모델 정의
class StockDataset(Dataset):
    def __init__(self, X, y):
        self.X = torch.tensor(X, dtype=torch.float32)
        self.y = torch.tensor(y, dtype=torch.float32)

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        return self.X[idx], self.y[idx]

class PositionalEncoding(nn.Module):
    def __init__(self, d_model, max_len=5000):
        super(PositionalEncoding, self).__init__()
        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * -(np.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        pe = pe.unsqueeze(1)
        self.register_buffer('pe', pe)

    def forward(self, x):
        return x + self.pe[:x.size(0), :]

class StockTransformer(nn.Module):
    def __init__(self, input_dim, output_dim, sequence_length, nhead=8, num_layers=3):
        super(StockTransformer, self).__init__()
        self.input_dim = input_dim
        self.sequence_length = sequence_length

        self.embedding = nn.Linear(input_dim, 128)
        self.positional_encoding = PositionalEncoding(128, max_len=sequence_length)

        encoder_layer = nn.TransformerEncoderLayer(d_model=128, nhead=nhead, batch_first=True)
        self.transformer_encoder = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)

        self.fc = nn.Sequential(
            nn.Flatten(),
            nn.Linear(128 * sequence_length, 64),
            nn.ReLU(),
            nn.Linear(64, output_dim)
        )

    def forward(self, x):
        x = self.embedding(x)
        x = self.positional_encoding(x.permute(1, 0, 2))
        x = x.permute(1, 0, 2)
        x = self.transformer_encoder(x)
        x = self.fc(x)
        return x

# Pydantic 모델
class StockPredictionRequest(BaseModel):
    symbols: List[str]
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    epochs: Optional[int] = 50
    
class StockPredictionResponse(BaseModel):
    symbol: str
    name: str
    predictions: Dict[str, Dict[str, float]]
    metrics: Dict[str, float]
    status: str

class TrainingStatus(BaseModel):
    task_id: str
    status: str
    progress: Optional[Dict[str, str]] = None
    results: Optional[List[StockPredictionResponse]] = None

# 백업용 주요 한국 주식
MAJOR_KOREAN_STOCKS = {
    # 시가총액 상위
    "005930": ("삼성전자", "KOSPI"),
    "000660": ("SK하이닉스", "KOSPI"),
    "207940": ("삼성바이오로직스", "KOSPI"),
    "005935": ("삼성전자우", "KOSPI"),
    "005490": ("POSCO홀딩스", "KOSPI"),
    "005380": ("현대차", "KOSPI"),
    "006400": ("삼성SDI", "KOSPI"),
    "035420": ("NAVER", "KOSPI"),
    "000270": ("기아", "KOSPI"),
    "003670": ("포스코퓨처엠", "KOSPI"),
    "105560": ("KB금융", "KOSPI"),
    "035720": ("카카오", "KOSPI"),
    "055550": ("신한지주", "KOSPI"),
    "066570": ("LG전자", "KOSPI"),
    "068270": ("셀트리온", "KOSPI"),
    "012330": ("현대모비스", "KOSPI"),
    "051910": ("LG화학", "KOSPI"),
    "028260": ("삼성물산", "KOSPI"),
    "086790": ("하나금융지주", "KOSPI"),
    "034730": ("SK", "KOSPI"),
    "003550": ("LG", "KOSPI"),
    "015760": ("한국전력", "KOSPI"),
    "009150": ("삼성전기", "KOSPI"),
    "316140": ("우리금융지주", "KOSPI"),
    "018260": ("삼성에스디에스", "KOSPI"),
    "010130": ("고려아연", "KOSPI"),
    "011200": ("HMM", "KOSPI"),
    "009540": ("HD한국조선해양", "KOSPI"),
    "024110": ("기업은행", "KOSPI"),
    "003490": ("대한항공", "KOSPI"),
    
    # 코스닥 대표
    "373220": ("LG에너지솔루션", "KOSPI"),
    "247540": ("에코프로비엠", "KOSDAQ"),
    "086520": ("에코프로", "KOSDAQ"),
    "328130": ("루닛", "KOSDAQ"),
    "357780": ("솔브레인", "KOSDAQ"),
    "393890": ("더블유씨피", "KOSDAQ"),
    "112040": ("위메이드", "KOSDAQ"),
    "263750": ("펄어비스", "KOSDAQ"),
    "293490": ("카카오게임즈", "KOSDAQ"),
    "036570": ("엔씨소프트", "KOSDAQ"),
}

# 비동기로 안전한 주식 목록 가져오기
async def get_stocks_async() -> pd.DataFrame:
    global stock_list_cache, stock_list_cache_time
    
    current_time = datetime.now()
    
    # 캐시 확인 (1시간)
    if stock_list_cache is not None and stock_list_cache_time:
        if (current_time - stock_list_cache_time).total_seconds() < 3600:
            logger.info(f"캐시 사용: {len(stock_list_cache)}개 종목")
            return stock_list_cache
    
    # 백업 데이터로 즉시 초기화
    backup_data = []
    for code, (name, market) in MAJOR_KOREAN_STOCKS.items():
        backup_data.append({
            'Code': code,
            'Name': name,
            'Market': market
        })
    
    backup_df = pd.DataFrame(backup_data)
    stock_list_cache = backup_df
    stock_list_cache_time = current_time
    
    logger.info("백업 데이터 사용")
    return backup_df

# 내부 사용
def get_stocks() -> pd.DataFrame:
    global stock_list_cache, stock_list_cache_time
    
    current_time = datetime.now()
    
    # 캐시 확인
    if stock_list_cache is not None and stock_list_cache_time:
        if (current_time - stock_list_cache_time).total_seconds() < 3600:
            return stock_list_cache
    
    # 백업 데이터 사용
    backup_data = []
    for code, (name, market) in MAJOR_KOREAN_STOCKS.items():
        backup_data.append({
            'Code': code,
            'Name': name,
            'Market': market
        })
    
    backup_df = pd.DataFrame(backup_data)
    stock_list_cache = backup_df
    stock_list_cache_time = current_time
    return backup_df

# 종목명 가져오기
def get_name(symbol: str) -> str:
    try:
        stock_list = get_stocks()
        result = stock_list[stock_list['Code'] == symbol]
        if not result.empty:
            return result.iloc[0]['Name']
    except:
        pass
    
    # 백업에서 찾기
    if symbol in MAJOR_KOREAN_STOCKS:
        return MAJOR_KOREAN_STOCKS[symbol][0]
    
    return symbol

# 한국 주식 검색
def search_stock(query: str, limit: int = 20) -> List[Dict]:
    results = []
    
    try:
        stock_list = get_stocks()
        query_upper = query.strip().upper()
        
        # 코드 검색
        if query.isdigit():
            padded = query.zfill(6)
            # 정확한 매칭
            exact = stock_list[stock_list['Code'] == padded]
            if not exact.empty:
                for _, row in exact.iterrows():
                    results.append({
                        "symbol": row['Code'],
                        "name": row['Name'],
                        "market": row.get('Market', 'KRX')
                    })
            else:
                # 부분 매칭
                partial = stock_list[stock_list['Code'].str.contains(query, na=False)]
                for _, row in partial.head(limit).iterrows():
                    results.append({
                        "symbol": row['Code'],
                        "name": row['Name'],
                        "market": row.get('Market', 'KRX')
                    })
        else:
            # 이름 검색
            name_matches = stock_list[
                stock_list['Name'].str.contains(query, case=False, na=False)
            ]
            for _, row in name_matches.head(limit).iterrows():
                results.append({
                    "symbol": row['Code'],
                    "name": row['Name'],
                    "market": row.get('Market', 'KRX')
                })
        
        logger.info(f"검색 '{query}': {len(results)}개 결과")
        
    except Exception as e:
        logger.error(f"검색 오류: {e}")
    
    return results

# 캐시를 활용한 데이터 가져오기
def get_data_cached(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    cache_key = f"{symbol}_{start_date}_{end_date}"
    current_time = datetime.now()
    
    # 캐시 확인 (10분)
    if cache_key in data_cache and cache_key in data_cache_time:
        if (current_time - data_cache_time[cache_key]).seconds < 600:
            logger.info(f"캐시 사용: {symbol}")
            return data_cache[cache_key]
    
    # 새로 가져오기
    data = get_data(symbol, start_date, end_date)
    data_cache[cache_key] = data
    data_cache_time[cache_key] = current_time
    return data

# 데이터 수집 (FDR 우선)
def get_data(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    symbol = symbol.zfill(6)
    logger.info(f"데이터 수집: {symbol}")
    
    # 1. FDR 시도 (한국 주식)
    try:
        fdr_limiter.wait()
        data = fdr.DataReader(symbol, start_date, end_date)
        if data is not None and not data.empty:
            logger.info(f"FDR 성공: {symbol}")
            return data
    except Exception as e:
        logger.warning(f"FDR 실패 {symbol}: {e}")
    
    # 2. 야후 파이낸스 백업 
    for suffix in ['.KS', '.KQ']:
        try:
            yf_limiter.wait()
            ticker = f"{symbol}{suffix}"
            stock = yf.Ticker(ticker)
            data = stock.history(period="max", start=start_date, end=end_date, timeout=5)
            if not data.empty:
                logger.info(f"YF {suffix} 성공: {symbol}")
                return data
        except Exception as e:
            logger.debug(f"YF {suffix} 실패 {symbol}: {e}")
            continue
    
    # 모든 방법 실패
    logger.error(f"모든 소스 실패: {symbol}")
    raise HTTPException(status_code=404, detail=f"데이터 수집 실패: {symbol}")

# 데이터 준비 
def prepare(stock_df: pd.DataFrame) -> pd.DataFrame:
    # 기본 기술적 지표 추가
    stock_df['MA5'] = stock_df['Close'].rolling(window=5).mean()
    stock_df['MA20'] = stock_df['Close'].rolling(window=20).mean()
    stock_df['Volume_MA'] = stock_df['Volume'].rolling(window=5).mean()
    stock_df['Price_Change'] = stock_df['Close'].pct_change() * 100
    stock_df['Volume_Change'] = stock_df['Volume'].pct_change() * 100
    
    # RSI 계산
    delta = stock_df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / (loss + 1e-10)  # 0으로 나누기 방지
    stock_df['RSI'] = 100 - (100 / (1 + rs))
    
    stock_df = stock_df.fillna(0)
    return stock_df

# 트랜스포머 모델을 활용해서 주식 예측 (3일로)
def train(symbol: str, stock_df: pd.DataFrame, epochs: int = 50) -> Dict:
    # 데이터 준비
    stock_df = prepare(stock_df)
    
    # 사용할 컬럼
    feature_cols = ['Open', 'High', 'Low', 'Close', 'Volume', 
                    'MA5', 'MA20', 'Price_Change', 'RSI']
    available_cols = [col for col in feature_cols if col in stock_df.columns]
    
    # 최소 OHLC는 필요
    if not all(col in stock_df.columns for col in ['Open', 'High', 'Low', 'Close']):
        raise ValueError(f"필수 컬럼 부족: {symbol}")
    
    data = stock_df[available_cols].values
    
    # 스케일링
    scaler = MinMaxScaler()
    data_scaled = scaler.fit_transform(data)
    
    # 시퀀스 생성 (3일 예측)
    sequence_length = 20
    forecast_days = 3 # 7일에서 3일로 변경
    X, y = [], []
    
    for i in range(len(data_scaled) - sequence_length - forecast_days + 1):
        X.append(data_scaled[i:i+sequence_length])
        # 타겟은 OHLC만
        y.append(data_scaled[i+sequence_length:i+sequence_length+forecast_days, :4].flatten())
    
    if len(X) < 10:
        raise ValueError(f"학습 데이터 부족: {symbol}")
    
    X = np.array(X)
    y = np.array(y)
    
    # 데이터 분할
    split_ratio = 0.9
    split_index = int(len(X) * split_ratio)
    X_train, X_val = X[:split_index], X[split_index:]
    y_train, y_val = y[:split_index], y[split_index:]
    
    # 데이터로더
    train_dataset = StockDataset(X_train, y_train)
    val_dataset = StockDataset(X_val, y_val)
    train_loader = DataLoader(train_dataset, batch_size=16, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=16)
    
    # 모델 생성 (3일 예측용 출력 차원)
    input_dim = len(available_cols)
    output_dim = forecast_days * 4  # 3일 * 4(OHLC)
    model = StockTransformer(input_dim, output_dim, sequence_length, nhead=4, num_layers=2)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.0001)
    criterion = nn.MSELoss()
    
    # 학습
    best_val_loss = float('inf')
    best_model_state = None
    patience = 10
    no_improve = 0
    
    logger.info(f"학습 시작: {symbol}")
    
    for epoch in range(epochs):
        # Training
        model.train()
        train_loss = 0
        for X_batch, y_batch in train_loader:
            optimizer.zero_grad()
            y_pred = model(X_batch)
            loss = criterion(y_pred, y_batch)
            loss.backward()
            optimizer.step()
            train_loss += loss.item()
        
        # Validation
        model.eval()
        val_loss = 0
        with torch.no_grad():
            for X_batch, y_batch in val_loader:
                y_pred = model(X_batch)
                loss = criterion(y_pred, y_batch)
                val_loss += loss.item()
        
        avg_val_loss = val_loss / len(val_loader) if len(val_loader) > 0 else float('inf')
        
        if avg_val_loss < best_val_loss:
            best_val_loss = avg_val_loss
            best_model_state = model.state_dict().copy()
            no_improve = 0
        else:
            no_improve += 1
        
        if no_improve >= patience:
            logger.info(f"Early stopping at epoch {epoch+1}")
            break
        
        if (epoch + 1) % 10 == 0:
            logger.info(f"[{symbol}] Epoch {epoch+1}/{epochs}, Loss: {avg_val_loss:.4f}")
    
    # 최적 모델로 예측
    if best_model_state is not None:
        model.load_state_dict(best_model_state)
    model.eval()
    
    # 검증 메트릭
    predictions = []
    with torch.no_grad():
        for X_batch, _ in val_loader:
            y_pred = model(X_batch)
            predictions.append(y_pred.numpy())
    
    if predictions:
        predictions = np.vstack(predictions)
        mse = mean_squared_error(y_val, predictions)
        mae = mean_absolute_error(y_val, predictions)
    else:
        mse = mae = 0
    
    # 미래 예측
    scaler_output = MinMaxScaler()
    scaler_output.fit(stock_df[['Open', 'High', 'Low', 'Close']].values)
    
    last_sequence = data_scaled[-sequence_length:].reshape(1, sequence_length, -1)
    last_sequence_tensor = torch.tensor(last_sequence, dtype=torch.float32)
    
    with torch.no_grad():
        future_pred = model(last_sequence_tensor)
        future_pred = future_pred.numpy().reshape(forecast_days, 4)
        future_prices = scaler_output.inverse_transform(future_pred)
    
    # 날짜 생성
    last_date = stock_df.index[-1]
    future_dates = pd.date_range(start=last_date + timedelta(days=1), periods=forecast_days, freq='D')
    
    predictions_dict = {}
    for i, date in enumerate(future_dates):
        predictions_dict[date.strftime('%Y-%m-%d')] = {
            'Open': int(round(future_prices[i, 0])),
            'High': int(round(future_prices[i, 1])),
            'Low': int(round(future_prices[i, 2])),
            'Close': int(round(future_prices[i, 3]))
        }
    
    return {
        'predictions': predictions_dict,
        'metrics': {'MSE': float(mse), 'MAE': float(mae)}
    }

# 비동기 학습 태스크
training_tasks = {}

# 데이터 수집과 학습 분리
async def run_async(task_id: str, symbols: List[str], start_date: str, end_date: str, epochs: int):
    results = []
    
    # 1단계: 모든 데이터 먼저 수집 (병렬)
    training_tasks[task_id]['progress'] = {symbol: 'Collecting data' for symbol in symbols}
    stock_data = {}
    
    data_tasks = []
    for symbol in symbols:
        data_tasks.append(
            asyncio.get_event_loop().run_in_executor(
                executor, get_data_cached, symbol, start_date, end_date
            )
        )
    
    # 모든 데이터 수집 완료 대기
    for idx, symbol in enumerate(symbols):
        try:
            stock_data[symbol] = await data_tasks[idx]
            training_tasks[task_id]['progress'][symbol] = 'Data ready'
            logger.info(f"데이터 수집 완료: {symbol}")
        except Exception as e:
            logger.error(f"데이터 수집 실패 {symbol}: {e}")
            training_tasks[task_id]['progress'][symbol] = 'Failed'
            results.append(StockPredictionResponse(
                symbol=symbol,
                name=get_name(symbol),
                predictions={},
                metrics={'MSE': -1, 'MAE': -1},
                status=f'Data collection failed: {str(e)}'
            ))
    
    # 2단계: 학습 진행 (순차적)
    for symbol in symbols:
        if symbol not in stock_data:
            continue
            
        try:
            training_tasks[task_id]['progress'][symbol] = 'Processing'
            stock_name = get_name(symbol)
            
            result = await asyncio.get_event_loop().run_in_executor(
                executor, train, symbol, stock_data[symbol], epochs
            )
            
            results.append(StockPredictionResponse(
                symbol=symbol,
                name=stock_name,
                predictions=result['predictions'],
                metrics=result['metrics'],
                status='Success'
            ))
            
            training_tasks[task_id]['progress'][symbol] = 'Completed'
            logger.info(f"완료: {symbol}")
            
        except Exception as e:
            logger.error(f"학습 실패 {symbol}: {e}")
            results.append(StockPredictionResponse(
                symbol=symbol,
                name=get_name(symbol),
                predictions={},
                metrics={'MSE': -1, 'MAE': -1},
                status=f'Training failed: {str(e)}'
            ))
            training_tasks[task_id]['progress'][symbol] = 'Failed'
    
    training_tasks[task_id]['status'] = 'Completed'
    training_tasks[task_id]['results'] = results

# API 정보
@app.get("/")
async def root():
    return {
        "message": "Korean Stock Prediction Model",
        "version": "2.0",
        "status": "operational",
        "features": ["single prediction", "multiple prediction", "caching", "optimized data collection"]
    }

# 주식 목록 갱신
@app.post("/refresh-list")
async def refresh_list():
    try:
        global stock_list_cache, stock_list_cache_time
        
        old_cache_size = len(stock_list_cache) if stock_list_cache is not None else 0
        stock_list_cache = None
        stock_list_cache_time = None
        
        stock_list = await get_stocks_async()
        
        return {
            "message": "주식 목록 갱신 완료",
            "total_stocks": len(stock_list),
            "previous_count": old_cache_size,
            "source": "backup",
            "cached": stock_list_cache is not None,
            "cache_time": stock_list_cache_time.isoformat() if stock_list_cache_time else None
        }
    except Exception as e:
        logger.error(f"갱신 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# 주식 검색
@app.get("/search/{query}")
async def search(query: str, limit: int = 20):
    try:
        results = search_stock(query, limit)
        
        if not results:
            raise HTTPException(
                status_code=404,
                detail=f"'{query}'에 대한 검색 결과가 없습니다"
            )
        
        return {
            "query": query,
            "count": len(results),
            "results": results
        }
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"검색 오류: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# 시장별 종목 목록
@app.get("/stocks/{market}")
async def get_by_market(market: str = "KOSPI", page: int = 1, size: int = 50):
    try:
        stock_list = get_stocks()
        
        # 시장 필터링
        if market.upper() != "ALL":
            filtered_list = stock_list[stock_list['Market'] == market.upper()]
        else:
            filtered_list = stock_list
        
        # 페이지네이션
        start_idx = (page - 1) * size
        end_idx = start_idx + size
        
        paginated_list = filtered_list.iloc[start_idx:end_idx]
        
        stocks = []
        for _, row in paginated_list.iterrows():
            stocks.append({
                "code": row['Code'],
                "name": row['Name'],
                "market": row.get('Market', 'KRX')
            })
        
        return {
            "market": market,
            "page": page,
            "size": size,
            "total": len(filtered_list),
            "stocks": stocks
        }
        
    except Exception as e:
        logger.error(f"시장별 조회 오류: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# 인기 종목
@app.get("/popular")
async def get_popular(limit: int = 20):
    try:
        stock_list = get_stocks()
        
        # 상위 종목 선택
        stocks = []
        for _, row in stock_list.head(min(limit, len(stock_list))).iterrows():
            stocks.append({
                "code": row['Code'],
                "name": row['Name'],
                "market": row.get('Market', 'KRX')
            })
        
        return {
            "message": "주요 종목",
            "count": len(stocks),
            "stocks": stocks
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# 비동기 여러 종목 예측 
@app.post("/predict")
async def predict(request: StockPredictionRequest, background_tasks: BackgroundTasks):
    if len(request.symbols) > 5:
        raise HTTPException(status_code=400, detail="최대 5개 종목까지 가능합니다")
    
    if len(request.symbols) == 0:
        raise HTTPException(status_code=400, detail="최소 1개 이상의 종목이 필요합니다")
    
    end_date = request.end_date or datetime.now().strftime('%Y-%m-%d')
    start_date = request.start_date or (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    
    task_id = f"task_{datetime.now().timestamp()}"
    
    training_tasks[task_id] = {
        'status': 'Running',
        'progress': {symbol: 'Queued' for symbol in request.symbols},
        'results': None
    }
    
    background_tasks.add_task(
        run_async,
        task_id,
        request.symbols,
        start_date,
        end_date,
        request.epochs
    )
    
    return {
        "task_id": task_id,
        "message": f"{len(request.symbols)}개 종목 학습 시작",
        "symbols": request.symbols
    }

# 학습 상태 확인
@app.get("/status/{task_id}")
async def get_status(task_id: str):
    if task_id not in training_tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = training_tasks[task_id]
    return TrainingStatus(
        task_id=task_id,
        status=task['status'],
        progress=task['progress'],
        results=task.get('results')
    )

# 단일 종목 즉시 예측 (캐싱 활용)
@app.post("/predict-one/{symbol}")
async def predict_one(
    symbol: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    epochs: Optional[int] = 50
):
    try:
        end_date = end_date or datetime.now().strftime('%Y-%m-%d')
        start_date = start_date or (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
        
        stock_name = get_name(symbol)
        
        # 캐싱된 데이터 우선 사용
        stock_df = get_data_cached(symbol, start_date, end_date)
        
        result = train(symbol, stock_df, epochs)
        
        return StockPredictionResponse(
            symbol=symbol,
            name=stock_name,
            predictions=result['predictions'],
            metrics=result['metrics'],
            status='Success'
        )
    except Exception as e:
        logger.error(f"단일 예측 실패 {symbol}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# 완료된 태스크 삭제
@app.delete("/task/{task_id}")
async def delete_task(task_id: str):
    if task_id in training_tasks:
        del training_tasks[task_id]
        return {"message": f"Task {task_id} deleted"}
    else:
        raise HTTPException(status_code=404, detail="Task not found")

# 캐시 상태 확인
@app.get("/cache")
async def get_cache():
    return {
        "stock_list_cache": {
            "cached": stock_list_cache is not None,
            "size": len(stock_list_cache) if stock_list_cache is not None else 0,
            "cache_time": stock_list_cache_time.isoformat() if stock_list_cache_time else None
        },
        "data_cache": {
            "entries": len(data_cache),
            "symbols": list(set([key.split('_')[0] for key in data_cache.keys()]))
        },
        "training_tasks": {
            "active": sum(1 for t in training_tasks.values() if t['status'] == 'Running'),
            "completed": sum(1 for t in training_tasks.values() if t['status'] == 'Completed'),
            "total": len(training_tasks)
        }
    }

# 캐시 클리어
@app.post("/clear-cache")
async def clear_cache():
    global data_cache, data_cache_time
    
    old_size = len(data_cache)
    data_cache = {}
    data_cache_time = {}
    
    return {
        "message": "캐시 클리어 완료",
        "cleared_entries": old_size
    }

# 실행
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)