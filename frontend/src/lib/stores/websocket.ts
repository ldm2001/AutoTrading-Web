import { writable } from 'svelte/store';

export const priceConnected = writable(false);
export const tradeConnected = writable(false);

function createWs(path: string, connectedStore: typeof priceConnected) {
	let ws: WebSocket | null = null;
	let reconnectTimer: ReturnType<typeof setTimeout>;
	let pingTimer: ReturnType<typeof setInterval> | null = null;
	let attempts = 0;
	const maxAttempts = 10;
	// Set으로 변경 — 동일 핸들러 중복 등록 방지
	const handlers: Map<string, Set<(data: unknown) => void>> = new Map();

	function getUrl() {
		const proto = location.protocol === 'https:' ? 'wss' : 'ws';
		return `${proto}://${location.host}${path}`;
	}

	function connect() {
		if (ws?.readyState === WebSocket.OPEN) return;

		ws = new WebSocket(getUrl());

		ws.onopen = () => {
			connectedStore.set(true);
			attempts = 0;
			pingTimer = setInterval(() => {
				if (ws?.readyState === WebSocket.OPEN) ws.send('ping');
			}, 30_000);
		};

		ws.onmessage = (event) => {
			if (event.data === 'pong') return;
			try {
				const msg = JSON.parse(event.data);
				const fns = handlers.get(msg.type);
				if (fns) for (const fn of fns) fn(msg);
			} catch {
				// ignore non-JSON
			}
		};

		ws.onclose = () => {
			connectedStore.set(false);
			if (pingTimer) { clearInterval(pingTimer); pingTimer = null; }
			if (attempts < maxAttempts) {
				reconnectTimer = setTimeout(connect, Math.min(1000 * 2 ** attempts, 30_000));
				attempts++;
			}
		};

		ws.onerror = () => ws?.close();
	}

	// cleanup 함수 반환 — onMount 해제 시 호출해야 메모리 누수 방지
	function on(type: string, handler: (data: unknown) => void) {
		if (!handlers.has(type)) handlers.set(type, new Set());
		handlers.get(type)!.add(handler);
		return () => handlers.get(type)?.delete(handler);
	}

	function close() {
		clearTimeout(reconnectTimer);
		if (pingTimer) { clearInterval(pingTimer); pingTimer = null; }
		ws?.close();
	}

	return { connect, on, close };
}

export const priceWs = createWs('/ws/prices', priceConnected);
export const tradeWs = createWs('/ws/trades', tradeConnected);
