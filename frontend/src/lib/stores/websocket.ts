import { writable } from 'svelte/store';

// 가격/체결 WebSocket 연결 상태 스토어
export const priceConnected = writable(false);
export const tradeConnected = writable(false);

// WebSocket 연결 팩토리 — 자동 재연결·핑·프레임 배칭 포함
function sock(path: string, connectedStore: typeof priceConnected) {
	let ws: WebSocket | null = null;
	let reconnectTimer: ReturnType<typeof setTimeout>;
	let pingTimer: ReturnType<typeof setInterval> | null = null;
	let attempts = 0;
	const maxAttempts = 10;
	const handlers: Map<string, Set<(data: unknown) => void>> = new Map();

	// 프레임 배칭: 동일 타입 메시지를 RAF 단위로 묶어 한번만 dispatch
	const pendingMessages: Map<string, unknown> = new Map();
	let rafId: number | null = null;

	// 배칭된 메시지를 핸들러로 일괄 전달
	function flush() {
		rafId = null;
		for (const [type, msg] of pendingMessages) {
			const fns = handlers.get(type);
			if (fns) for (const fn of fns) fn(msg);
		}
		pendingMessages.clear();
	}

	// 같은 타입은 최신 메시지로 덮어써 다음 프레임에 flush
	function qmsg(msg: { type: string; [key: string]: unknown }) {
		// 같은 타입은 최신 메시지로 덮어씀 (배칭)
		pendingMessages.set(msg.type, msg);
		if (rafId === null) {
			rafId = requestAnimationFrame(flush);
		}
	}

	// 현재 호스트 기준 WS URL 생성
	function url() {
		const proto = location.protocol === 'https:' ? 'wss' : 'ws';
		return `${proto}://${location.host}${path}`;
	}

	// 연결 수립 + 핑/재연결/메시지 핸들러 등록
	function connect() {
		if (ws?.readyState === WebSocket.OPEN) return;

		ws = new WebSocket(url());

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
					qmsg(msg);
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

	// 타입별 메시지 핸들러 등록 (해제 함수 반환)
	function on(type: string, handler: (data: unknown) => void) {
		if (!handlers.has(type)) handlers.set(type, new Set());
		handlers.get(type)!.add(handler);
		return () => handlers.get(type)?.delete(handler);
	}

	// 연결 종료 + 타이머/배칭 정리
	function close() {
		clearTimeout(reconnectTimer);
		if (pingTimer) { clearInterval(pingTimer); pingTimer = null; }
		if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null; }
		pendingMessages.clear();
		ws?.close();
	}

	return { connect, on, close };
}

// 가격/체결 WS 인스턴스
export const priceWs = sock('/ws/prices', priceConnected);
export const tradeWs = sock('/ws/trades', tradeConnected);
