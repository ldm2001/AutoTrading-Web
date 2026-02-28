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

export interface DailyCandle {
	date: string;
	open: number;
	high: number;
	low: number;
	close: number;
	volume: number;
}

export interface MarketIndex {
	code: string;
	name: string;
	value: number;
	change: number;
	change_percent: number;
}

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

export interface TradingStatus {
	is_running: boolean;
	bought_list: string[];
	today_trades: TradeLog[];
}

export interface PriceUpdate {
	type: 'price_update';
	stocks: Stock[];
	indices: MarketIndex[];
}

export interface TradeMessage {
	type: 'message' | 'trade';
	data: string | TradeLog;
}

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

export interface NewsArticle {
	title: string;
	sentiment: string;
	score: number;
}

export interface NewsSentiment {
	code: string;
	name: string;
	score: number;
	summary: string;
	articles: NewsArticle[];
}

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

export interface Portfolio {
	items: HoldingItem[];
	total_eval: number;
	total_profit_loss: number;
	cash_balance: number;
}

export interface RecommendFactor {
	name: string;
	score: number;
	max: number;
	reason: string;
}

export interface RecommendPrediction {
	current_price: number;
	predicted_5d: number;
	change_pct: number;
	trend: string;
}

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

export interface OrderLevel {
	price: number;
	volume: number;
}

export interface OrderBook {
	asks: OrderLevel[];
	bids: OrderLevel[];
}

export interface HeatmapStock {
	code: string;
	name: string;
	profit_loss_pct: number;
}

export interface SectorCell {
	sector: string;
	weight_pct: number;
	avg_return: number;
	eval_amount: number;
	profit_loss: number;
	stocks: HeatmapStock[];
}

export interface PredictionCandle {
	date: string;
	open: number;
	high: number;
	low: number;
	close: number;
}

export interface PredictionResult {
	code: string;
	name: string;
	predictions: PredictionCandle[];
	metrics: { mae: number; accuracy_pct: number };
	status: string;
}
