import { writable, derived, get } from 'svelte/store';
import type { Stock, MarketIndex, DailyCandle } from '$lib/types';

const SELECTED_KEY = 'selected_stock';

// localStorage에서 마지막 선택 종목 복원
function sel0(): string {
	try {
		return localStorage.getItem(SELECTED_KEY) || '';
	} catch {}
	return '';
}

// 선택 종목 localStorage 저장
function selx(code: string) {
	try {
		localStorage.setItem(SELECTED_KEY, code);
	} catch {}
}

// 상장사 목록 항목 타입
export interface StockListItem {
	code: string;
	name: string;
	market: string;
}

// 전체 상장사 목록 (코드+이름+마켓)
export const allStocks = writable<StockListItem[]>([]);
// 선택된 종목의 상세 정보
export const selectedStockDetail = writable<Stock | null>(null);
// 시장 지수 스토어
export const indices = writable<MarketIndex[]>([]);
// 현재 선택 종목 코드 (localStorage 동기화)
export const selectedStock = writable<string>(sel0() || '005930');
// 선택 종목 일봉 스토어
export const dailyCandles = writable<DailyCandle[]>([]);

selectedStock.subscribe(selx);

// 가격 데이터가 로드된 종목들 (WebSocket 업데이트 호환)
export const stocks = writable<Stock[]>([]);
// 종목코드 → Stock 매핑
export const stockMap = derived(stocks, ($stocks) => {
	const map = new Map<string, Stock>();
	for (const s of $stocks) map.set(s.code, s);
	return map;
});

// 종목코드 → 상장사 항목 매핑
export const allStockMap = derived(allStocks, ($all) => {
	const map = new Map<string, StockListItem>();
	for (const s of $all) map.set(s.code, s);
	return map;
});

// 전체 상장사 목록 로드 + 기본 선택 보정
export async function stockinit() {
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

// 단일 종목 현재가 조회 + 스토어 갱신
export async function quote(code: string): Promise<Stock | null> {
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

// 전체 지수 조회
export async function idxq() {
	const resp = await fetch('/api/stocks/index/all');
	if (resp.ok) indices.set(await resp.json());
}

// 종목 일봉 조회
export async function dayq(code: string) {
	const resp = await fetch(`/api/stocks/${code}/daily`);
	if (resp.ok) dailyCandles.set(await resp.json());
}

// 검색어로 종목 현재가 조회
export async function stockq(query: string): Promise<Stock | null> {
	const resp = await fetch(`/api/stocks/search/${encodeURIComponent(query)}/price`);
	if (resp.ok) return await resp.json();
	return null;
}

// 검색 자동완성 항목 타입
export interface SearchSuggestion {
	code: string;
	name: string;
}

// 검색어 자동완성 후보 조회
export async function hintq(query: string): Promise<SearchSuggestion[]> {
	if (query.length < 1) return [];
	const resp = await fetch(`/api/stocks/search?q=${encodeURIComponent(query)}`);
	if (resp.ok) return await resp.json();
	return [];
}

// WS 가격 업데이트를 보유 목록/선택 종목에 병합
export function mergeq(incoming: Stock[]) {
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
