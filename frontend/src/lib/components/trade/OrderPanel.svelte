<script lang="ts">
	import { onMount } from 'svelte';
	import { selectedStock, stockMap } from '$lib/stores/stocks';
	import { tradingStatus, addConsoleMessage } from '$lib/stores/trading';
	import type { OrderBook } from '$lib/types';
	import './OrderPanel.css';

	let { onclose }: { onclose?: () => void } = $props();

	let tab      = $state<'buy' | 'sell'>('buy');
	let histTab  = $state<'done' | 'pending'>('done');
	let qty      = $state(0);
	let price    = $state(0);
	let loading  = $state(false);
	let result   = $state<{ type: 'success' | 'error'; msg: string } | null>(null);
	let book     = $state<OrderBook | null>(null);
	let cash     = $state(0);
	let bookTimer: ReturnType<typeof setInterval> | null = null;

	const stockInfo    = $derived($stockMap.get($selectedStock));
	const currentPrice = $derived(stockInfo?.price ?? 0);
	const maxQty       = $derived(price > 0 ? Math.floor(cash / price) : 0);
	const orderTotal   = $derived(price * qty);

	// 수익 계산 (매수 기준, 수수료 0.015% + 세금 0.23%)
	const BUY_FEE  = 0.00015;
	const SELL_FEE = 0.00245;
	const targetPrice  = $derived(price > 0 ? Math.round(price * 1.05) : 0);
	const stopPrice    = $derived(price > 0 ? Math.round(price * 0.97) : 0);
	const targetProfit = $derived(price > 0 ? Math.round(targetPrice - price - price * BUY_FEE - targetPrice * SELL_FEE) : 0);
	const stopLoss     = $derived(price > 0 ? Math.round(stopPrice  - price - price * BUY_FEE - stopPrice  * SELL_FEE) : 0);
	const targetPct    = $derived(price > 0 ? targetProfit / price * 100 : 0);
	const stopPct      = $derived(price > 0 ? stopLoss     / price * 100 : 0);

	// 종목 변경 -> 초기화 + 호가 재조회
	$effect(() => {
		const code = $selectedStock;
		price = currentPrice;
		qty   = 0;
		result = null;
		if (code) loadBook(code);
	});

	async function loadBook(code: string) {
		try {
			const r = await fetch(`/api/stocks/${code}/orderbook`);
			if (r.ok) book = await r.json();
		} catch { /* ignore */ }
	}

	async function loadCash() {
		try {
			const r = await fetch('/api/trading/balance');
			if (r.ok) { const d = await r.json(); cash = d.cash ?? 0; }
		} catch { /* ignore */ }
	}

	onMount(() => {
		loadCash();
		bookTimer = setInterval(() => {
			if ($selectedStock) loadBook($selectedStock);
		}, 3000);
		return () => { if (bookTimer) clearInterval(bookTimer); };
	});

	// 호가 클릭 -> 가격 자동 입력
	function pickPrice(p: number) { price = p; }

	// 수량 비율 설정 (잔고 기준)
	function setQtyPct(pct: number) {
		if (!price || !cash) return;
		qty = Math.max(1, Math.floor(cash * pct / price));
	}

	// 최대 거래량 기준 bar width
	function barWidth(vol: number, side: 'ask' | 'bid'): number {
		if (!book) return 0;
		const list = side === 'ask' ? book.asks : book.bids;
		const max  = Math.max(...list.map(l => l.volume), 1);
		return Math.round(vol / max * 100);
	}

	async function submit() {
		if (!$selectedStock || qty <= 0 || price <= 0) return;
		loading = true;
		result  = null;
		try {
			const resp = await fetch(`/api/trading/${tab}`, {
				method:  'POST',
				headers: { 'Content-Type': 'application/json' },
				body:    JSON.stringify({ code: $selectedStock, qty }),
			});
			const data = await resp.json();
			if (resp.ok && data.success) {
				const label = tab === 'buy' ? '매수' : '매도';
				result = { type: 'success', msg: `${label} 성공: ${stockInfo?.name ?? ''} ${qty}주` };
				addConsoleMessage(`[수동 ${label}] ${stockInfo?.name ?? $selectedStock} ${qty}주`);
				await loadCash();
			} else {
				result = { type: 'error', msg: data.detail || '주문 실패' };
			}
		} catch {
			result = { type: 'error', msg: '네트워크 오류' };
		} finally {
			loading = false;
		}
	}

	function fmt(n: number) { return n.toLocaleString('ko-KR'); }
</script>

<div class="op-wrap">
	<!-- 헤더 -->
	<div class="op-head">
		<span class="op-title">주식주문</span>
		{#if stockInfo}
			<span class="op-stock">{stockInfo.name}</span>
			<span class="op-code">{$selectedStock} {stockInfo.market}</span>
		{/if}
		{#if onclose}
			<button class="op-close" onclick={onclose}>✕</button>
		{/if}
	</div>

	{#if stockInfo}
		<div class="op-body">
			<!-- ── 호가창 ── -->
			<div class="ob-col">
				<div class="ob-header">
					<span>매도호가</span>
					<span>거래량</span>
				</div>

				<!-- 매도호가 (위=높은가격) -->
				{#if book}
					{#each book.asks as row}
						<button class="ob-row ask" onclick={() => pickPrice(row.price)}>
							<span class="ob-bar ask" style="width:{barWidth(row.volume,'ask')}%"></span>
							<span class="ob-price ask">{fmt(row.price)}</span>
							<span class="ob-vol">{fmt(row.volume)}</span>
						</button>
					{/each}
				{:else}
					{#each Array(5) as _}
						<div class="ob-row ask skeleton"></div>
					{/each}
				{/if}

				<!-- 현재가 구분선 -->
				<div class="ob-cur">
					<span class="ob-cur-price" class:up={currentPrice > 0 && stockInfo.change > 0} class:down={stockInfo.change < 0}>
						{fmt(currentPrice)}
					</span>
					<span class="ob-cur-chg" class:up={stockInfo.change > 0} class:down={stockInfo.change < 0}>
						{stockInfo.change > 0 ? '▲' : stockInfo.change < 0 ? '▼' : ''}{Math.abs(stockInfo.change_percent).toFixed(2)}%
					</span>
				</div>

				<!-- 매수호가 (위=높은가격) -->
				{#if book}
					{#each book.bids as row}
						<button class="ob-row bid" onclick={() => pickPrice(row.price)}>
							<span class="ob-bar bid" style="width:{barWidth(row.volume,'bid')}%"></span>
							<span class="ob-price bid">{fmt(row.price)}</span>
							<span class="ob-vol">{fmt(row.volume)}</span>
						</button>
					{/each}
				{:else}
					{#each Array(5) as _}
						<div class="ob-row bid skeleton"></div>
					{/each}
				{/if}
			</div>

			<!-- ── 주문 폼 ── -->
			<div class="of-col">
				<div class="of-form">
					<!-- 매수/매도 탭 -->
					<div class="of-tabs">
						<button class="of-tab" class:active={tab==='buy'} class:buy={tab==='buy'} onclick={() => tab='buy'}>매수</button>
						<button class="of-tab" class:active={tab==='sell'} class:sell={tab==='sell'} onclick={() => tab='sell'}>매도</button>
					</div>

					<!-- 가격 -->
					<div class="of-row">
						<span class="of-label">가격</span>
						<div class="of-input-wrap">
							<input type="number" bind:value={price} class="of-input" min="0" />
							<span class="of-unit">원</span>
							<div class="of-arrows">
								<button onclick={() => price += 1}>∧</button>
								<button onclick={() => price = Math.max(0, price - 1)}>∨</button>
							</div>
						</div>
					</div>
					<p class="of-hint">✓ [거래량 있음] 주문 시 체결 처리 됩니다</p>

					<!-- 수량 -->
					<div class="of-row">
						<span class="of-label">수량</span>
						<span class="of-max">최대 {fmt(maxQty)}주</span>
					</div>
					<div class="of-row">
						<div class="of-input-wrap qty">
							<input type="number" bind:value={qty} class="of-input" min="0" />
							<span class="of-unit">주</span>
							<div class="of-arrows">
								<button onclick={() => qty += 1}>∧</button>
								<button onclick={() => qty = Math.max(0, qty - 1)}>∨</button>
							</div>
						</div>
					</div>

					<!-- % 버튼 -->
					<div class="of-pct-row">
						{#each [10,25,50,100] as pct}
							<button class="of-pct" onclick={() => setQtyPct(pct/100)}>{pct}%</button>
						{/each}
					</div>

					<!-- 주문총액 -->
					<div class="of-summary">
						<span class="of-summary-label">최대 {fmt(cash)}원</span>
					</div>
					<div class="of-total-row">
						<span class="of-label">주문총액</span>
						<span class="of-total">{fmt(orderTotal)}원</span>
					</div>

					<!-- 주문 버튼 -->
					<button
						class="of-submit"
						class:buy={tab==='buy'}
						class:sell={tab==='sell'}
						onclick={submit}
						disabled={loading || qty <= 0}
					>
						{loading ? '처리중' : tab === 'buy' ? '매수' : '매도'}
					</button>

					{#if result}
						<div class="of-result" class:success={result.type==='success'} class:error={result.type==='error'}>
							{result.msg}
						</div>
					{/if}

					<!-- 수익 계산 -->
					{#if price > 0}
						<div class="of-calc">
							<div class="of-calc-title">수익를 계산 <span class="of-calc-fee-label">(수수료 포함)</span></div>
							<div class="of-calc-row">
								<span class="of-calc-label">목표가</span>
								<span class="of-calc-val">{fmt(targetPrice)}</span>
								<span class="of-calc-pnl up">+{fmt(targetProfit)}원 (+{targetPct.toFixed(2)}%)</span>
							</div>
							<div class="of-calc-row">
								<span class="of-calc-label">손절가</span>
								<span class="of-calc-val">{fmt(stopPrice)}</span>
								<span class="of-calc-pnl down">{fmt(stopLoss)}원 ({stopPct.toFixed(2)}%)</span>
							</div>
							<div class="of-calc-fee-row">수수료 0.015% + 세금 0.23%</div>
						</div>
					{/if}
				</div>

				<!-- ── 체결/미체결 탭 (주문폼 바로 아래) ── -->
				<div class="hist-tabs">
					<button class="hist-tab" class:active={histTab==='done'} onclick={() => histTab='done'}>체결 내역</button>
					<button class="hist-tab" class:active={histTab==='pending'} onclick={() => histTab='pending'}>미체결 내역</button>
				</div>
				<div class="hist-body">
					{#if histTab === 'done'}
						{#if $tradingStatus.today_trades.length === 0}
							<div class="hist-empty">거래 내역이 없습니다</div>
						{:else}
							{#each $tradingStatus.today_trades as t}
								<div class="hist-row">
									<span class="hist-time">{t.time.split(' ')[1] ?? t.time}</span>
									<span class="hist-type" class:buy={t.type==='buy'} class:sell={t.type==='sell'}>
										{t.type === 'buy' ? '매수' : '매도'}
									</span>
									<span class="hist-name">{t.name || t.code}</span>
									<span class="hist-qty">{t.qty}주</span>
									<span class="hist-ok" class:ok={t.success} class:fail={!t.success}>
										{t.success ? '체결' : '실패'}
									</span>
								</div>
							{/each}
						{/if}
					{:else}
						<div class="hist-empty">미체결 내역이 없습니다</div>
					{/if}
				</div>
			</div>
		</div>
	{:else}
		<div class="op-empty">종목을 선택하세요</div>
	{/if}
</div>
