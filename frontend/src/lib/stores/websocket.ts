import { writable } from 'svelte/store';

export const priceConnected = writable(false);
export const tradeConnected = writable(false);

function createWs(path: string, connectedStore: typeof priceConnected) {
	let ws: WebSocket | null = null;
	let reconnectTimer: ReturnType<typeof setTimeout>;
	let pingTimer: ReturnType<typeof setInterval> | null = null;
	let attempts = 0;
	const maxAttempts = 10;
	const handlers: Map<string, Set<(data: unknown) => void>> = new Map();

	// 프레임 배칭: 동일 타입 메시지를 RAF 단위로 묶어 한번만 dispatch
	const pendingMessages: Map<string, unknown> = new Map();
	let rafId: number | null = null;

	function flushMessages() {
		rafId = null;
		for (const [type, msg] of pendingMessages) {
			const fns = handlers.get(type);
			if (fns) for (const fn of fns) fn(msg);
		}
		pendingMessages.clear();
	}

	function queueMessage(msg: { type: string; [key: string]: unknown }) {
		// 같은 타입은 최신 메시지로 덮어씀 (배칭)
		pendingMessages.set(msg.type, msg);
		if (rafId === null) {
			rafId = requestAnimationFrame(flushMessages);
		}
	}

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
				// trade 이벤트는 즉시 dispatch (지연 불가)
				if (msg.type === 'trade' || msg.type === 'message') {
					const fns = handlers.get(msg.type);
					if (fns) for (const fn of fns) fn(msg);
				} else {
					// price_update 등은 프레임 배칭
					queueMessage(msg);
				}
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

	function on(type: string, handler: (data: unknown) => void) {
		if (!handlers.has(type)) handlers.set(type, new Set());
		handlers.get(type)!.add(handler);
		return () => handlers.get(type)?.delete(handler);
	}

	function close() {
		clearTimeout(reconnectTimer);
		if (pingTimer) { clearInterval(pingTimer); pingTimer = null; }
		if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null; }
		pendingMessages.clear();
		ws?.close();
	}

	return { connect, on, close };
}

export const priceWs = createWs('/ws/prices', priceConnected);
export const tradeWs = createWs('/ws/trades', tradeConnected);
