import { writable } from 'svelte/store';

export const priceConnected = writable(false);
export const tradeConnected = writable(false);

function createWs(path: string, connectedStore: typeof priceConnected) {
	let ws: WebSocket | null = null;
	let reconnectTimer: ReturnType<typeof setTimeout>;
	let attempts = 0;
	const maxAttempts = 10;
	const handlers: Map<string, ((data: unknown) => void)[]> = new Map();

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
			// Heartbeat
			const ping = setInterval(() => {
				if (ws?.readyState === WebSocket.OPEN) ws.send('ping');
				else clearInterval(ping);
			}, 30_000);
		};

		ws.onmessage = (event) => {
			if (event.data === 'pong') return;
			try {
				const msg = JSON.parse(event.data);
				const fns = handlers.get(msg.type) || [];
				for (const fn of fns) fn(msg);
			} catch {
				// ignore non-JSON
			}
		};

		ws.onclose = () => {
			connectedStore.set(false);
			if (attempts < maxAttempts) {
				reconnectTimer = setTimeout(connect, Math.min(1000 * 2 ** attempts, 30_000));
				attempts++;
			}
		};

		ws.onerror = () => ws?.close();
	}

	function on(type: string, handler: (data: unknown) => void) {
		if (!handlers.has(type)) handlers.set(type, []);
		handlers.get(type)!.push(handler);
	}

	function close() {
		clearTimeout(reconnectTimer);
		ws?.close();
	}

	return { connect, on, close };
}

export const priceWs = createWs('/ws/prices', priceConnected);
export const tradeWs = createWs('/ws/trades', tradeConnected);
