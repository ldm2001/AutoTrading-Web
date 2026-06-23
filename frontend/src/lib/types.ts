// 종목 현재가/등락 정보
export interface Stock {
	code: string;
	name: string;
	price: number;
	change: number;
	change_percent: number;
	volume: number;
	market_cap: string;
	market: string;
}

// 일봉 캔들
export interface DailyCandle {
	date: string;
	open: number;
	high: number;
	low: number;
	close: number;
	volume: number;
}

// 시장 지수
export interface MarketIndex {
	code: string;
	name: string;
	value: number;
	change: number;
	change_percent: number;
}

// 체결 로그
export interface TradeLog {
	time: string;
	code: string;
	name: string;
	type: 'buy' | 'sell';
	qty: number;
	price: number;
	success: boolean;
	message: string;
}

// 봇 실행 상태 + 전략 플랜
export interface TradingStatus {
	is_running: boolean;
	bought_list: string[];
	today_trades: TradeLog[];
	watch_count?: number;
	plan?: {
		target_buy_count: number;
		buy_percent: number;
		stop_loss_pct: number;
		take_profit_pct: number;
		buy_score_threshold: number;
	};
}

// WS 가격 업데이트 메시지
export interface PriceUpdate {
	type: 'price_update';
	stocks: Stock[];
	indices: MarketIndex[];
}

// WS 체결/메시지 이벤트
export interface TradeMessage {
	type: 'message' | 'trade';
	data: string | TradeLog;
}

// AI 종합 시그널 (지표 포함)
export interface AISignal {
	code: string;
	name: string;
	signal: 'buy' | 'hold' | 'sell';
	confidence: number;
	reasons: string[];
	indicators: {
		rsi: number | null;
		macd: { macd: number; signal: number; histogram: number } | null;
		bollinger: { upper: number; middle: number; lower: number; current_price: number } | null;
		price: number | null;
		volume: number | null;
	};
	news_count: number;
}

// 뉴스 기사 1건
export interface NewsArticle {
	title: string;
	sentiment: string;
	score: number;
}

// 뉴스 감성 분석 결과
export interface NewsSentiment {
	code: string;
	name: string;
	score: number;
	summary: string;
	articles: NewsArticle[];
}

// 보유 종목 항목
export interface HoldingItem {
	code: string;
	name: string;
	qty: number;
	avg_price: number;
	current_price: number;
	eval_amount: number;
	profit_loss: number;
	profit_loss_percent: number;
}

// 포트폴리오 요약
export interface Portfolio {
	items: HoldingItem[];
	total_eval: number;
	total_profit_loss: number;
	cash_balance: number;
}

// 추천 팩터 (이름·점수·근거)
export interface RecommendFactor {
	name: string;
	score: number;
	max: number;
	reason: string;
}

// 추천 예측 요약 (5일)
export interface RecommendPrediction {
	current_price: number;
	predicted_5d: number;
	change_pct: number;
	trend: string;
}

// 추천 종목
export interface RecommendStock {
	code: string;
	name: string;
	signal: 'buy' | 'sell' | 'hold';
	score: number;
	price: number;
	summary: string;
	factors: RecommendFactor[];
	prediction?: RecommendPrediction | null;
}

// 추천 응답 (보강 진행 상태 포함)
export interface RecommendResponse {
	items: RecommendStock[];
	loading: boolean;
	refreshing: boolean;
}

// 호가 1레벨
export interface OrderLevel {
	price: number;
	volume: number;
}

// 호가창 (매도/매수)
export interface OrderBook {
	asks: OrderLevel[];
	bids: OrderLevel[];
}

// 히트맵 내 종목
export interface HeatmapStock {
	code: string;
	name: string;
	profit_loss_pct: number;
}

// 섹터 히트맵 셀
export interface SectorCell {
	sector: string;
	weight_pct: number;
	avg_return: number;
	eval_amount: number;
	profit_loss: number;
	stocks: HeatmapStock[];
}

// 예측 캔들
export interface PredictionCandle {
	date: string;
	open: number;
	high: number;
	low: number;
	close: number;
}

// 예측 결과 (메트릭 포함)
export interface PredictionResult {
	code: string;
	name: string;
	predictions: PredictionCandle[];
	metrics: { mae: number; accuracy_pct: number };
	status: string;
}
