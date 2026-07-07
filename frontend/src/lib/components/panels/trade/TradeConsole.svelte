<script lang="ts">
	// 자동매매 콘솔 — 실시간 로그·긴급정지·당일 체결 내역
	import { tradingStatus, consoleMessages, boff } from '$lib/stores/trading';
	import './TradeConsole.css';

	let consoleEl: HTMLDivElement;
	let tradesOpen = $state(false);
	let stopping = $state(false);

	// 새 메시지 도착 시 콘솔 맨 아래로 자동 스크롤
	$effect(() => {
		if ($consoleMessages.length && consoleEl) {
			consoleEl.scrollTop = consoleEl.scrollHeight;
		}
	});

	// 파생 상태 — 당일 체결 수
	const tradeCount = $derived($tradingStatus.today_trades.length);

	// 로그 파싱 — [YYYY-MM-DD HH:MM:SS] 접두어를 시각/본문 컬럼으로 분리 (개행 보존)
	const LOG_RE = /^\[\d{4}-\d{2}-\d{2} (\d{2}:\d{2}:\d{2})\]\s?([\s\S]*)$/;
	const logLines = $derived(
		$consoleMessages.map((raw) => {
			const hit = LOG_RE.exec(raw);
			const body = hit ? hit[2] : raw;
			return { time: hit ? hit[1] : '', body, section: body.startsWith('====') };
		})
	);

	// 자동매매 전체 긴급정지
	async function halt() {
		if (!$tradingStatus.is_running || stopping) return;
		stopping = true;
		await boff();
		stopping = false;
	}
</script>

<div class="console-wrap">
	<div class="console-header">
		<span class="console-title">Console</span>
		<div class="console-head-right">
			<button
				class="console-stop"
				type="button"
				onclick={halt}
				disabled={!$tradingStatus.is_running || stopping}
			>
				{stopping ? '정지중' : '전체 긴급정지'}
			</button>
		</div>
	</div>

	<div class="console-bar">
		<div class="console-tags">
			<span class="console-tag strategy">멀티팩터</span>
			{#if $tradingStatus.plan}
				<span class="console-tag">종목당 {($tradingStatus.plan.buy_percent * 100).toFixed(0)}%</span>
				<span class="console-tag">손절 {$tradingStatus.plan.stop_loss_pct}%</span>
				<span class="console-tag">익절 +{$tradingStatus.plan.take_profit_pct}%</span>
				<span class="console-tag">스캔 {$tradingStatus.watch_count ?? 0}개</span>
			{/if}
		</div>
	</div>

	<div bind:this={consoleEl} class="console-log">
		{#if logLines.length === 0}
			<p class="console-placeholder">상단에서 스위치를 활성화하세요.</p>
		{/if}
		{#each logLines as line}
			<div class="console-msg" class:section={line.section}>
				{#if line.time}<span class="log-time">{line.time}</span>{/if}
				<span class="log-body">{line.body}</span>
			</div>
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
