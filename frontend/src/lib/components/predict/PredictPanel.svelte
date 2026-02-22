<script lang="ts">
	import { prediction, predictionLoading, fetchPrediction } from '$lib/stores/predict';
	import { selectedStock, stockMap } from '$lib/stores/stocks';
	import './PredictPanel.css';

	let lastCode = '';

	$effect(() => {
		const code = $selectedStock;
		if (code && code !== lastCode) {
			lastCode = code;
			fetchPrediction(code);
		}
	});

	const stockInfo = $derived($stockMap.get($selectedStock));
	const lastClose = $derived(stockInfo?.price ?? 0);

	function fmtPrice(n: number): string {
		return n.toLocaleString('ko-KR');
	}

	function changeFrom(close: number, base: number): { value: number; pct: number } {
		const value = close - base;
		const pct = base > 0 ? (value / base) * 100 : 0;
		return { value, pct };
	}
</script>

<div class="predict-panel">
	{#if $predictionLoading}
		<div class="predict-loading">
			<div class="predict-spinner"></div>
			<span>Transformer 모델 학습 중...</span>
			<span class="predict-progress">1년치 데이터 수집 → 학습 → 5일 예측 (약 10~30초)</span>
		</div>
	{:else if $prediction && $prediction.predictions.length > 0}
		<div class="predict-metrics">
			<div class="metric-item">
				<span class="metric-label">모델</span>
				<span class="metric-value">Transformer</span>
			</div>
			<div class="metric-item">
				<span class="metric-label">MAE</span>
				<span class="metric-value">{$prediction.metrics.mae.toFixed(4)}</span>
			</div>
			<div class="metric-item">
				<span class="metric-label">정확도</span>
				<span class="metric-value">{$prediction.metrics.accuracy_pct}%</span>
			</div>
		</div>

		<table class="predict-table">
			<thead>
				<tr>
					<th>날짜</th>
					<th>시가</th>
					<th>고가</th>
					<th>저가</th>
					<th>종가</th>
					<th>전일대비</th>
				</tr>
			</thead>
			<tbody>
				{#each $prediction.predictions as candle, i}
					{@const base = i === 0 ? lastClose : $prediction.predictions[i - 1].close}
					{@const diff = changeFrom(candle.close, base)}
					<tr>
						<td>{candle.date}</td>
						<td>{fmtPrice(candle.open)}</td>
						<td class="td-up">{fmtPrice(candle.high)}</td>
						<td class="td-down">{fmtPrice(candle.low)}</td>
						<td>{fmtPrice(candle.close)}</td>
						<td class:td-up={diff.value > 0} class:td-down={diff.value < 0}>
							{diff.value > 0 ? '+' : ''}{fmtPrice(diff.value)}
							({diff.pct > 0 ? '+' : ''}{diff.pct.toFixed(2)}%)
						</td>
					</tr>
				{/each}
			</tbody>
		</table>

		<p class="predict-note">
			* Transformer Encoder 모델 기반 예측이며 투자 참고용입니다
		</p>
	{:else}
		<div class="predict-empty">종목을 선택하면 5일 예측이 시작됩니다</div>
	{/if}
</div>
