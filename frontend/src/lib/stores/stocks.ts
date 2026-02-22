import { writable, derived, get } from 'svelte/store';
import type { Stock, MarketIndex, DailyCandle } from '$lib/types';

const SELECTED_KEY = 'selected_stock';

function loadSelected(): string {
	try {
		return localStorage.getItem(SELECTED_KEY) || '';
	} catch {}
	return '';
}

function saveSelected(code: string) {
	try {
		localStorage.setItem(SELECTED_KEY, code);
	} catch {}
}

export interface StockListItem {
	code: string;
	name: string;
	market: string;
}

// 전체 상장사 목록 (코드+이름+마켓)
export const allStocks = writable<StockListItem[]>([]);
// 선택된 종목의 상세 정보
export const selectedStockDetail = writable<Stock | null>(null);
export const indices = writable<MarketIndex[]>([]);
export const selectedStock = writable<string>(loadSelected() || '005930');
export const dailyCandles = writable<DailyCandle[]>([]);

selectedStock.subscribe(saveSelected);

// 가격 데이터가 로드된 종목들 (WebSocket 업데이트 호환)
export const stocks = writable<Stock[]>([]);
export const stockMap = derived(stocks, ($stocks) => {
	const map = new Map<string, Stock>();
	for (const s of $stocks) map.set(s.code, s);
	return map;
});

export const allStockMap = derived(allStocks, ($all) => {
	const map = new Map<string, StockListItem>();
	for (const s of $all) map.set(s.code, s);
	return map;
});

export async function initAllStocks() {
	const resp = await fetch('/api/stocks');
	if (resp.ok) {
		const list: StockListItem[] = await resp.json();
		allStocks.set(list);

		const sel = get(selectedStock);
		if (!list.some((s) => s.code === sel) && list.length > 0) {
			selectedStock.set(list[0].code);
		}
	}
}

export async function fetchStockPrice(code: string): Promise<Stock | null> {
	try {
		const resp = await fetch(`/api/stocks/${code}/price`);
		if (resp.ok) {
			const stock: Stock = await resp.json();
			selectedStockDetail.set(stock);
			// stocks store에도 추가/업데이트
			stocks.update((list) => {
				const idx = list.findIndex((s) => s.code === code);
				if (idx >= 0) {
					list[idx] = stock;
					return [...list];
				}
				return [...list, stock];
			});
			return stock;
		}
	} catch {}
	return null;
}

export async function fetchIndices() {
	const resp = await fetch('/api/stocks/index/all');
	if (resp.ok) indices.set(await resp.json());
}

export async function fetchDailyCandles(code: string) {
	const resp = await fetch(`/api/stocks/${code}/daily`);
	if (resp.ok) dailyCandles.set(await resp.json());
}

export async function searchStock(query: string): Promise<Stock | null> {
	const resp = await fetch(`/api/stocks/search/${encodeURIComponent(query)}/price`);
	if (resp.ok) return await resp.json();
	return null;
}

export interface SearchSuggestion {
	code: string;
	name: string;
}

export async function searchSuggestions(query: string): Promise<SearchSuggestion[]> {
	if (query.length < 1) return [];
	const resp = await fetch(`/api/stocks/search?q=${encodeURIComponent(query)}`);
	if (resp.ok) return await resp.json();
	return [];
}

export function updateStockPrices(incoming: Stock[]) {
	const priceMap = new Map(incoming.map((s) => [s.code, s]));
	stocks.update((list) =>
		list.map((s) => priceMap.get(s.code) ?? s)
	);
	// 선택된 종목 가격도 업데이트
	const sel = get(selectedStock);
	const updated = priceMap.get(sel);
	if (updated) {
		selectedStockDetail.set(updated);
	}
}
