<script lang="ts">
	import { aiSignal, aiLoading, fetchAISignal, fetchNewsSentiment } from '$lib/stores/ai';
	import { selectedStock } from '$lib/stores/stocks';
	import './AISignalPanel.css';

	let lastCode = '';

	$effect(() => {
		const code = $selectedStock;
		if (code && code !== lastCode) {
			lastCode = code;
			fetchAISignal(code);
			fetchNewsSentiment(code);
		}
	});

	function signalLabel(signal: string): string {
		switch (signal) {
			case 'buy': return '매수';
			case 'sell': return '매도';
			default: return '관망';
		}
	}

	function rsiLabel(rsi: number): string {
		if (rsi >= 70) return '과매수';
		if (rsi <= 30) return '과매도';
		return '중립';
	}
</script>

<div class="ai-panel">
	<div class="ai-panel-header">
		<span class="ai-panel-title">AI 분석</span>
		{#if $aiSignal}
			<span class="ai-stock-name">{$aiSignal.name}</span>
		{/if}
	</div>

	{#if $aiLoading}
		<div class="ai-loading">
			<div class="ai-spinner"></div>
			<span>AI 분석 중...</span>
		</div>
	{:else if $aiSignal}
		<div class="ai-signal-row">
			<div class="ai-signal-badge" class:buy={$aiSignal.signal === 'buy'} class:sell={$aiSignal.signal === 'sell'} class:hold={$aiSignal.signal === 'hold'}>
				{signalLabel($aiSignal.signal)}
			</div>
			<div class="ai-confidence">
				<span class="ai-confidence-label">확신도</span>
				<div class="ai-confidence-bar">
					<div class="ai-confidence-fill" style="transform: scaleX({$aiSignal.confidence / 100})"
						class:high={$aiSignal.confidence >= 70}
						class:mid={$aiSignal.confidence >= 40 && $aiSignal.confidence < 70}
						class:low={$aiSignal.confidence < 40}
					></div>
				</div>
				<span class="ai-confidence-value">{$aiSignal.confidence}%</span>
			</div>
		</div>

		<div class="ai-reasons">
			{#each $aiSignal.reasons as reason}
				<div class="ai-reason-item">{reason}</div>
			{/each}
		</div>

		<div class="ai-indicators">
			{#if $aiSignal.indicators.rsi !== null}
				<div class="ai-indicator">
					<span class="ai-ind-label">RSI(14)</span>
					<span class="ai-ind-value" class:over-buy={$aiSignal.indicators.rsi >= 70} class:over-sell={$aiSignal.indicators.rsi <= 30}>
						{$aiSignal.indicators.rsi} ({rsiLabel($aiSignal.indicators.rsi)})
					</span>
				</div>
			{/if}
			{#if $aiSignal.indicators.macd}
				<div class="ai-indicator">
					<span class="ai-ind-label">MACD</span>
					<span class="ai-ind-value" class:up={$aiSignal.indicators.macd.histogram > 0} class:down={$aiSignal.indicators.macd.histogram < 0}>
						{$aiSignal.indicators.macd.histogram > 0 ? '+' : ''}{$aiSignal.indicators.macd.histogram}
					</span>
				</div>
			{/if}
			{#if $aiSignal.indicators.bollinger}
				<div class="ai-indicator">
					<span class="ai-ind-label">볼린저</span>
					<span class="ai-ind-value">
						{$aiSignal.indicators.bollinger.lower.toLocaleString()} ~ {$aiSignal.indicators.bollinger.upper.toLocaleString()}
					</span>
				</div>
			{/if}
		</div>
	{:else}
		<div class="ai-empty">종목을 선택하면 AI 분석이 시작됩니다</div>
	{/if}
</div>
