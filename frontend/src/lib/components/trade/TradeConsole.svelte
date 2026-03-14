<script lang="ts">
	import { selectedStock, stockMap } from '$lib/stores/stocks';
	import { tradingStatus, consoleMessages, watchCodes, stopBot } from '$lib/stores/trading';
	import './TradeConsole.css';

	let consoleEl: HTMLDivElement;
	let tradesOpen = $state(false);
	let stopping = $state(false);

	$effect(() => {
		if ($consoleMessages.length && consoleEl) {
			consoleEl.scrollTop = consoleEl.scrollHeight;
		}
	});

	const tradeCount = $derived($tradingStatus.today_trades.length);
	const stockInfo = $derived($stockMap.get($selectedStock));
	const autoOn = $derived.by(() => !!$selectedStock && $watchCodes.includes($selectedStock));
	const autoLive = $derived.by(() => autoOn && $tradingStatus.is_running);

	async function emergencyStop() {
		if (!$tradingStatus.is_running || stopping) return;
		stopping = true;
		await stopBot();
		stopping = false;
	}
</script>

<div class="console-wrap">
	<div class="console-header">
		<span class="console-title">Console</span>
		<div class="console-head-right">
			<span class="console-status" class:running={$tradingStatus.is_running}>
				{$tradingStatus.is_running ? 'LIVE' : 'IDLE'}
			</span>
			<button
				class="console-stop"
				type="button"
				onclick={emergencyStop}
				disabled={!$tradingStatus.is_running || stopping}
			>
				{stopping ? '정지중' : '전체 긴급정지'}
			</button>
		</div>
	</div>

	<div class="console-bar">
		<div class="console-tags">
			<span class="console-tag strategy">멀티팩터</span>
			<span class="console-tag" class:auto={autoOn} class:live={autoLive}>
				{stockInfo?.name ?? '선택 종목'} · {autoLive ? '자동매매 중' : autoOn ? '자동대기' : '수동'}
			</span>
			{#if $tradingStatus.plan}
				<span class="console-tag">종목당 {($tradingStatus.plan.buy_percent * 100).toFixed(0)}%</span>
				<span class="console-tag">손절 {$tradingStatus.plan.stop_loss_pct}%</span>
				<span class="console-tag">익절 +{$tradingStatus.plan.take_profit_pct}%</span>
				<span class="console-tag">스캔 {$tradingStatus.watch_count ?? 0}개</span>
			{/if}
		</div>
	</div>

	<div bind:this={consoleEl} class="console-log">
		{#if $consoleMessages.length === 0}
			<p class="console-placeholder">상단에서 스위치를 활성화하세요.</p>
		{/if}
		{#each $consoleMessages as msg}
			<p class="console-msg">{msg}</p>
		{/each}
	</div>

	{#if tradeCount > 0}
		<button class="trades-toggle" onclick={() => tradesOpen = !tradesOpen}>
			<span>Today's Trades ({tradeCount})</span>
			<span class="trades-arrow" class:open={tradesOpen}>▾</span>
		</button>

		{#if tradesOpen}
			<div class="trades-list">
				{#each $tradingStatus.today_trades as trade}
					<div class="trade-row">
						<span class="trade-time">{trade.time.split(' ')[1]}</span>
						<span class="trade-type" class:buy={trade.type === 'buy'} class:sell={trade.type === 'sell'}>
							{trade.type === 'buy' ? '매수' : '매도'}
						</span>
						<span class="trade-name">{trade.name || trade.code}</span>
						<span class="trade-qty">{trade.qty}주</span>
						<span class="trade-result" class:ok={trade.success} class:fail={!trade.success}>
							{trade.success ? '✓' : '✗'}
						</span>
					</div>
				{/each}
			</div>
		{/if}
	{/if}
</div>
