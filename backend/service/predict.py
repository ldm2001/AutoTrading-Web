# Transformer 인코더 기반 주가 예측 서비스
import logging
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_absolute_error

logger = logging.getLogger(__name__)


# 시계열 데이터셋
class StockDataset(Dataset):
    def __init__(self, X: np.ndarray, y: np.ndarray):
        self.X = torch.tensor(X, dtype=torch.float32)
        self.y = torch.tensor(y, dtype=torch.float32)

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        return self.X[idx], self.y[idx]


# 시퀀스 위치 인코딩
class PositionalEncoding(nn.Module):
    def __init__(self, d_model: int, max_len: int = 5000):
        super().__init__()
        pe       = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(
            torch.arange(0, d_model, 2).float() * -(np.log(10000.0) / d_model)
        )
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        pe = pe.unsqueeze(1)
        self.register_buffer("pe", pe)

    def forward(self, x):
        return x + self.pe[: x.size(0), :]


# Transformer 인코더 기반 주가 예측 모델
class StockTransformer(nn.Module):
    def __init__(
        self,
        input_dim: int,
        output_dim: int,
        sequence_length: int,
        nhead: int = 4,
        num_layers: int = 2,
    ):
        super().__init__()
        self.embedding           = nn.Linear(input_dim, 128)
        self.positional_encoding = PositionalEncoding(128, max_len=sequence_length)
        encoder_layer            = nn.TransformerEncoderLayer(
            d_model=128, nhead=nhead, batch_first=True
        )
        self.transformer_encoder = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        self.fc = nn.Sequential(
            nn.Flatten(),
            nn.Linear(128 * sequence_length, 64),
            nn.ReLU(),
            nn.Linear(64, output_dim),
        )

    def forward(self, x):
        x = self.embedding(x)
        x = self.positional_encoding(x.permute(1, 0, 2))
        x = x.permute(1, 0, 2)
        x = self.transformer_encoder(x)
        return self.fc(x)


class Predictor:

    def __init__(self):
        self._cache:      dict[str, dict]  = {}
        self._cache_time: dict[str, float] = {}
        self._executor    = ThreadPoolExecutor(max_workers=2)
        self._CACHE_TTL   = 3600  # 1시간

    # 1년치 주가 데이터 수집 (FDR 우선, YFinance 폴백)
    def _fetch(self, symbol: str) -> pd.DataFrame:
        symbol     = symbol.zfill(6)
        end_date   = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        # FDR 시도
        try:
            import FinanceDataReader as fdr
            data = fdr.DataReader(symbol, start_date, end_date)
            if data is not None and not data.empty:
                logger.info(f"FDR 데이터 수집 성공: {symbol} ({len(data)}일)")
                return data
        except Exception as e:
            logger.warning(f"FDR 실패 {symbol}: {e}")
        # YFinance 폴백
        try:
            import yfinance as yf
            for suffix in [".KS", ".KQ"]:
                ticker = f"{symbol}{suffix}"
                stock  = yf.Ticker(ticker)
                data   = stock.history(start=start_date, end=end_date, timeout=10)
                if not data.empty:
                    logger.info(f"YF 데이터 수집 성공: {ticker} ({len(data)}일)")
                    return data
        except Exception as e:
            logger.warning(f"YFinance 실패 {symbol}: {e}")
        raise ValueError(f"데이터 수집 실패: {symbol}")

    # 피처 엔지니어링 (MA5, MA20, RSI, 변화율)
    def _prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df                  = df.copy()
        df["MA5"]           = df["Close"].rolling(window=5).mean()
        df["MA20"]          = df["Close"].rolling(window=20).mean()
        df["Price_Change"]  = df["Close"].pct_change() * 100
        df["Volume_Change"] = df["Volume"].pct_change() * 100
        # RSI
        delta = df["Close"].diff()
        gain  = delta.where(delta > 0, 0).rolling(window=14).mean()
        loss  = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs    = gain / (loss + 1e-10)
        df["RSI"] = 100 - (100 / (1 + rs))
        df = df.fillna(0)
        return df

    # Transformer 학습 및 5일 예측 (동기 실행, ThreadPool 에서 호출)
    def _run(
        self,
        symbol: str,
        forecast_days: int = 5,
        epochs: int = 30,
        sequence_length: int = 20,
    ) -> dict:
        start_t  = time.time()
        stock_df = self._fetch(symbol)
        stock_df = self._prepare(stock_df)

        feature_cols   = ["Open", "High", "Low", "Close", "Volume", "MA5", "MA20", "Price_Change", "RSI"]
        available_cols = [c for c in feature_cols if c in stock_df.columns]

        if not all(c in stock_df.columns for c in ["Open", "High", "Low", "Close"]):
            raise ValueError(f"필수 컬럼 부족: {symbol}")

        data        = stock_df[available_cols].values
        scaler      = MinMaxScaler()
        data_scaled = scaler.fit_transform(data)

        # 시퀀스 생성
        X, y = [], []
        for i in range(len(data_scaled) - sequence_length - forecast_days + 1):
            X.append(data_scaled[i: i + sequence_length])
            y.append(
                data_scaled[i + sequence_length: i + sequence_length + forecast_days, :4].flatten()
            )

        if len(X) < 10:
            raise ValueError(f"학습 데이터 부족: {symbol}")

        X = np.array(X)
        y = np.array(y)

        # 학습/검증 분할 (90:10)
        split        = int(len(X) * 0.9)
        X_train, X_val = X[:split], X[split:]
        y_train, y_val = y[:split], y[split:]
        train_loader = DataLoader(StockDataset(X_train, y_train), batch_size=16, shuffle=True)
        val_loader   = DataLoader(StockDataset(X_val,   y_val),   batch_size=16)

        # 모델 초기화
        input_dim  = len(available_cols)
        output_dim = forecast_days * 4
        model      = StockTransformer(input_dim, output_dim, sequence_length)
        optimizer  = torch.optim.Adam(model.parameters(), lr=0.0001)
        criterion  = nn.MSELoss()

        best_val_loss        = float("inf")
        best_state           = None
        patience, no_improve = 10, 0

        # 학습 루프 (Early Stopping 포함)
        for epoch in range(epochs):
            model.train()
            for X_b, y_b in train_loader:
                optimizer.zero_grad()
                loss = criterion(model(X_b), y_b)
                loss.backward()
                optimizer.step()
            model.eval()
            val_loss = 0.0
            with torch.no_grad():
                for X_b, y_b in val_loader:
                    val_loss += criterion(model(X_b), y_b).item()
            avg_val = val_loss / max(len(val_loader), 1)
            if avg_val < best_val_loss:
                best_val_loss = avg_val
                best_state    = {k: v.clone() for k, v in model.state_dict().items()}
                no_improve    = 0
            else:
                no_improve += 1
            if no_improve >= patience:
                logger.info(f"[{symbol}] Early stop at epoch {epoch + 1}")
                break

        if best_state:
            model.load_state_dict(best_state)
        model.eval()

        # 검증 MAE
        preds = []
        with torch.no_grad():
            for X_b, _ in val_loader:
                preds.append(model(X_b).numpy())
        mae = float(mean_absolute_error(y_val, np.vstack(preds))) if preds else 0.0

        # 미래 5일 예측값 역변환
        scaler_out   = MinMaxScaler()
        scaler_out.fit(stock_df[["Open", "High", "Low", "Close"]].values)
        last_seq     = data_scaled[-sequence_length:].reshape(1, sequence_length, -1)
        with torch.no_grad():
            future        = model(torch.tensor(last_seq, dtype=torch.float32))
            future        = future.numpy().reshape(forecast_days, 4)
            future_prices = scaler_out.inverse_transform(future)

        # 영업일 기준 날짜 생성
        last_date    = stock_df.index[-1]
        future_dates = pd.bdate_range(start=last_date + timedelta(days=1), periods=forecast_days)

        predictions = [
            {
                "date":  d.strftime("%Y-%m-%d"),
                "open":  int(round(future_prices[i, 0])),
                "high":  int(round(future_prices[i, 1])),
                "low":   int(round(future_prices[i, 2])),
                "close": int(round(future_prices[i, 3])),
            }
            for i, d in enumerate(future_dates)
        ]

        elapsed  = time.time() - start_t
        accuracy = max(0, round((1 - mae) * 100, 1))
        logger.info(f"[{symbol}] 예측 완료: {elapsed:.1f}초, MAE={mae:.4f}")

        return {
            "predictions": predictions,
            "metrics":     {"mae": round(mae, 4), "accuracy_pct": accuracy},
        }

    # 비동기 예측 진입점 (캐시 포함)
    async def predict(self, symbol: str) -> dict:
        symbol = symbol.zfill(6)
        now    = time.time()
        if symbol in self._cache and now - self._cache_time.get(symbol, 0) < self._CACHE_TTL:
            logger.info(f"캐시 사용: {symbol}")
            return self._cache[symbol]
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(self._executor, self._run, symbol)
        self._cache[symbol]      = result
        self._cache_time[symbol] = now
        return result


# 모듈 레벨 인스턴스
predictor = Predictor()

# 하위 호환 별칭
predict_stock = predictor.predict
