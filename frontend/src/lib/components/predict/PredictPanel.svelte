<script lang="ts">
	// 예측 패널 — Transformer 5일 캔들 예측
	import { prediction, predictionLoading, predq } from '$lib/stores/predict';
	import { selectedStock, stockMap } from '$lib/stores/stocks';
	import './PredictPanel.css';

	let lastCode = '';

	// 종목 변경 시 예측 조회
	$effect(() => {
		const code = $selectedStock;
		if (code && code !== lastCode) {
			lastCode = code;
			predq(code);
		}
	});

	// 선택 종목 정보 / 최근 종가 (예측 기준선)
	const stockInfo = $derived($stockMap.get($selectedStock));
	const lastClose = $derived(stockInfo?.price ?? 0);

	// 원화 콤마 포맷
	function won(n: number): string {
		return n.toLocaleString('ko-KR');
	}

	// 전일 대비 변화량/변화율
	function diff(close: number, base: number): { value: number; pct: number } {
		const value = close - base;
		const pct = base > 0 ? (value / base) * 100 : 0;
		return { value, pct };
	}
</script>

<div class="predict-panel">
	{#if $predictionLoading}
		<div class="predict-loading">
			<div class="predict-spinner"></div>
			<span>모델 학습 중</span>
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
				<span class="metric-label" title="정규화(MinMax) 데이터 기준 1−MAE 지표 — 투자 예측 정확도가 아님">적합도(참고)</span>
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
					{@const d = diff(candle.close, base)}
					<tr>
						<td>{candle.date}</td>
						<td>{won(candle.open)}</td>
						<td class="td-up">{won(candle.high)}</td>
						<td class="td-down">{won(candle.low)}</td>
						<td>{won(candle.close)}</td>
						<td class:td-up={d.value > 0} class:td-down={d.value < 0}>
							{d.value > 0 ? '+' : ''}{won(d.value)}
							({d.pct > 0 ? '+' : ''}{d.pct.toFixed(2)}%)
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
