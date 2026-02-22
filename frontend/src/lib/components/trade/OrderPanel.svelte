<script lang="ts">
	import { selectedStock, stockMap } from '$lib/stores/stocks';
	import { addConsoleMessage } from '$lib/stores/trading';
	import './OrderPanel.css';

	let tab = $state<'buy' | 'sell'>('buy');
	let qty = $state(1);
	let targetPrice = $state(0);
	let stopPrice = $state(0);
	let loading = $state(false);
	let result = $state<{ type: 'success' | 'error'; message: string } | null>(null);

	// 수수료율: 매수 0.015% + 매도 0.015% + 세금 0.23%
	const FEE_RATE = 0.0003; // 매수+매도 수수료
	const TAX_RATE = 0.0023; // 매도 세금

	const stockInfo = $derived($stockMap.get($selectedStock));
	const currentPrice = $derived(stockInfo?.price ?? 0);
	const totalEstimate = $derived(currentPrice * qty);

	// 목표가 수익률 (수수료 포함)
	const targetReturn = $derived(() => {
		if (!currentPrice || !targetPrice || targetPrice <= 0) return null;
		const buyFee = currentPrice * qty * (FEE_RATE / 2);
		const sellFee = targetPrice * qty * (FEE_RATE / 2);
		const tax = targetPrice * qty * TAX_RATE;
		const profit = (targetPrice - currentPrice) * qty - buyFee - sellFee - tax;
		const pct = (profit / (currentPrice * qty)) * 100;
		return { profit: Math.round(profit), pct: pct.toFixed(2) };
	});

	// 손절가 손실률 (수수료 포함)
	const stopReturn = $derived(() => {
		if (!currentPrice || !stopPrice || stopPrice <= 0) return null;
		const buyFee = currentPrice * qty * (FEE_RATE / 2);
		const sellFee = stopPrice * qty * (FEE_RATE / 2);
		const tax = stopPrice * qty * TAX_RATE;
		const profit = (stopPrice - currentPrice) * qty - buyFee - sellFee - tax;
		const pct = (profit / (currentPrice * qty)) * 100;
		return { profit: Math.round(profit), pct: pct.toFixed(2) };
	});

	// 현재가 변경시 목표가/손절가 자동 설정
	$effect(() => {
		if (currentPrice > 0 && targetPrice === 0) {
			targetPrice = Math.round(currentPrice * 1.05);
			stopPrice = Math.round(currentPrice * 0.97);
		}
	});

	// 종목 변경시 초기화
	$effect(() => {
		$selectedStock; // 의존성 추적
		targetPrice = 0;
		stopPrice = 0;
		result = null;
		qty = 1;
	});

	function setQtyPercent(pct: number) {
		// 간단한 비율 설정 (실제로는 잔고 기반이어야 하지만 UI 표시용)
		qty = Math.max(1, Math.round(qty * pct));
	}

	async function order(side: 'buy' | 'sell') {
		if (!$selectedStock || qty <= 0) return;
		loading = true;
		result = null;

		try {
			const resp = await fetch(`/api/trading/${side}`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ code: $selectedStock, qty }),
			});
			const data = await resp.json();

			if (resp.ok && data.success) {
				const action = side === 'buy' ? '매수' : '매도';
				result = { type: 'success', message: `${action} 성공: ${stockInfo?.name ?? $selectedStock} ${qty}주` };
				addConsoleMessage(`[수동 ${action}] ${stockInfo?.name ?? $selectedStock} ${qty}주`);
			} else {
				result = { type: 'error', message: data.detail || '주문 실패' };
			}
		} catch {
			result = { type: 'error', message: '네트워크 오류' };
		} finally {
			loading = false;
		}
	}
</script>

<div class="order-panel">
	<div class="order-header">
		<span class="order-title">주식주문</span>
	</div>

	{#if stockInfo}
		<div class="order-stock-info">
			<span class="order-stock-name">{stockInfo.name}</span>
			<span class="order-stock-code">{$selectedStock} {stockInfo.market}</span>
		</div>

		<!-- 매수/매도 탭 -->
		<div class="order-tabs">
			<button class="order-tab" class:active={tab === 'buy'} class:buy={tab === 'buy'} onclick={() => tab = 'buy'}>
				매수
			</button>
			<button class="order-tab" class:active={tab === 'sell'} class:sell={tab === 'sell'} onclick={() => tab = 'sell'}>
				매도
			</button>
		</div>

		<!-- 현재가 -->
		<div class="order-row">
			<span class="order-label">현재가</span>
			<span class="order-price" class:up={stockInfo.change > 0} class:down={stockInfo.change < 0}>
				{currentPrice.toLocaleString()}원
			</span>
		</div>

		<!-- 수량 -->
		<div class="order-row">
			<span class="order-label">수량</span>
			<div class="order-qty-group">
				<button class="order-qty-btn" onclick={() => qty = Math.max(1, qty - 1)} disabled={loading}>-</button>
				<input
					type="number"
					min="1"
					bind:value={qty}
					class="order-qty-input"
					disabled={loading}
				/>
				<button class="order-qty-btn" onclick={() => qty += 1} disabled={loading}>+</button>
			</div>
		</div>

		<!-- 예상금액 -->
		<div class="order-row">
			<span class="order-label">예상 금액</span>
			<span class="order-value">{totalEstimate.toLocaleString()}원</span>
		</div>

		<!-- 주문 버튼 -->
		<button
			class="order-submit"
			class:buy={tab === 'buy'}
			class:sell={tab === 'sell'}
			onclick={() => order(tab)}
			disabled={loading}
		>
			{loading ? '처리중...' : tab === 'buy' ? '매수' : '매도'}
		</button>

		{#if result}
			<div class="order-result" class:success={result.type === 'success'} class:error={result.type === 'error'}>
				{result.message}
			</div>
		{/if}

		<!-- 수익률 계산기 (주문 버튼 바로 아래) -->
		<div class="calc-section">
			<div class="calc-title">수익률 계산 (수수료 포함)</div>

			<div class="calc-row">
				<span class="calc-label">목표가</span>
				<input type="number" bind:value={targetPrice} class="calc-input" />
			</div>
			{#if targetReturn()}
				{@const tr = targetReturn()!}
				<div class="calc-result" class:positive={tr.profit > 0} class:negative={tr.profit < 0}>
					{tr.profit > 0 ? '+' : ''}{tr.profit.toLocaleString()}원 ({tr.profit > 0 ? '+' : ''}{tr.pct}%)
				</div>
			{/if}

			<div class="calc-row">
				<span class="calc-label">손절가</span>
				<input type="number" bind:value={stopPrice} class="calc-input" />
			</div>
			{#if stopReturn()}
				{@const sr = stopReturn()!}
				<div class="calc-result" class:positive={sr.profit > 0} class:negative={sr.profit < 0}>
					{sr.profit > 0 ? '+' : ''}{sr.profit.toLocaleString()}원 ({sr.profit > 0 ? '+' : ''}{sr.pct}%)
				</div>
			{/if}

			<div class="calc-note">
				수수료 0.015% + 세금 0.23%
			</div>
		</div>
	{:else}
		<div class="order-empty">종목을 선택하세요</div>
	{/if}
</div>
