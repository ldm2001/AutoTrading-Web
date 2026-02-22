<script lang="ts">
	import { tradingStatus, consoleMessages } from '$lib/stores/trading';
	import './TradeConsole.css';

	let consoleEl: HTMLDivElement;
	let tradesOpen = $state(false);

	$effect(() => {
		if ($consoleMessages.length && consoleEl) {
			consoleEl.scrollTop = consoleEl.scrollHeight;
		}
	});

	const tradeCount = $derived($tradingStatus.today_trades.length);
</script>

<div class="console-wrap">
	<div class="console-header">
		<span class="console-title">Console</span>
		<span class="console-status" class:running={$tradingStatus.is_running}>
			{$tradingStatus.is_running ? 'LIVE' : 'IDLE'}
		</span>
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
