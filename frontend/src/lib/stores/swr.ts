// SWR 패턴 — stale-while-revalidate 서버 상태 캐시
import { writable, type Writable } from 'svelte/store';

interface SWRState<T> {
	data: T | null;
	error: string | null;
	loading: boolean;
	lastFetched: number;
}

interface SWROptions {
	ttl?: number; // 캐시 유효 시간 (ms), 기본 30초
	dedupe?: number; // 중복 요청 방지 시간 (ms), 기본 2초
}

const cache = new Map<string, { store: Writable<SWRState<unknown>>; fetching: boolean; lastFetch: number }>();

export function swr<T>(
	key: string,
	fetcher: () => Promise<T>,
	options: SWROptions = {}
) {
	const { ttl = 30_000, dedupe = 2_000 } = options;

	let entry = cache.get(key);
	if (!entry) {
		entry = {
			store: writable<SWRState<unknown>>({ data: null, error: null, loading: false, lastFetched: 0 }),
			fetching: false,
			lastFetch: 0,
		};
		cache.set(key, entry);
	}

	const { store } = entry;

	async function revalidate(force = false) {
		const now = Date.now();
		const e = cache.get(key)!;

		// dedupe: 최근 요청 방지
		if (!force && e.fetching) return;
		if (!force && now - e.lastFetch < dedupe) return;

		e.fetching = true;
		e.lastFetch = now;

		store.update(s => ({ ...s, loading: !s.data, error: null }));

		try {
			const data = await fetcher();
			store.set({ data: data as unknown, error: null, loading: false, lastFetched: now });
		} catch (err) {
			store.update(s => ({ ...s, error: String(err), loading: false }));
		} finally {
			e.fetching = false;
		}
	}

	// stale check — ttl 초과 시 자동 revalidate
	function get(): Writable<SWRState<T>> {
		const e = cache.get(key)!;
		const now = Date.now();
		if (now - e.lastFetch > ttl) {
			revalidate();
		}
		return store as Writable<SWRState<T>>;
	}

	// 캐시 무효화
	function invalidate() {
		cache.delete(key);
	}

	// 옵티미스틱 업데이트
	function mutate(updater: (current: T | null) => T) {
		store.update(s => ({ ...s, data: updater(s.data as T | null) as unknown }));
	}

	return { subscribe: (store as Writable<SWRState<T>>).subscribe, revalidate, get, invalidate, mutate };
}
