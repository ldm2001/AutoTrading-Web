<script lang="ts">
	// 예측 패널 — Transformer 5일 종가 예측 (기준 종가 대비 누적 수익률)
	import { prediction, predictionError, predictionLoading, predq, skillnote } from '$lib/stores/predict';
	import { selectedStock } from '$lib/stores/stocks';
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

	// 예측 기준선 — 학습에 쓴 확정 일봉의 마지막 종가와 날짜 (현재가 아님)
	const baseClose = $derived($prediction?.base_close ?? 0);
	const baseDate = $derived($prediction?.base_date ?? '');
	// 0% 예측 대비 개선율 — 0 이하면 기준선(변화 없음 예측)보다 나쁨
	const skill = $derived(skillnote($prediction?.metrics?.skill));

	// 원화 콤마 포맷
	function won(n: number): string {
		return n.toLocaleString('ko-KR');
	}

	// %p 표시 — 숫자가 아니면 '-' (지표 없는 옛 형식 응답에서 패널이 깨지지 않게)
	function pts(n: number | null | undefined): string {
		return typeof n === 'number' && Number.isFinite(n) ? `${n.toFixed(2)}%p` : '-';
	}

	// 부호 붙은 % 표시 — 숫자가 아니면 '-'
	function pctlabel(n: number | null | undefined): string {
		if (typeof n !== 'number' || !Number.isFinite(n)) return '-';
		return `${n > 0 ? '+' : ''}${n.toFixed(2)}%`;
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
	{:else if $predictionError}
		<div class="predict-error">{$predictionError}</div>
	{:else if $prediction && $prediction.code === $selectedStock && $prediction.predictions.length > 0}
		<!-- 응답 종목이 지금 선택 종목일 때만 그림 (다른 종목의 예측 표시 방지) -->
		<!-- 검증 지표 — 검증 구간(재학습 전)의 5일 누적 수익률 기준, 0% 예측(변화 없음)과 비교 -->
		<div class="predict-metrics">
			<div class="metric-item">
				<span class="metric-label" title="검증 구간의 5일 누적 수익률 평균절대오차 (%p)">검증 오차 (모델)</span>
				<span class="metric-value">{pts($prediction.metrics.mae)}</span>
				<span class="metric-sub">0% 예측 {pts($prediction.metrics.mae_zero)}</span>
			</div>
			<div class="metric-item">
				<span class="metric-label" title="1 − 모델 오차 / 0% 예측 오차">0% 예측 대비 개선율</span>
				<span class="metric-value" class:metric-worse={skill.worse}>{skill.text}</span>
				{#if skill.worse}
					<span class="metric-sub metric-worse">기준선(변화 없음 예측)보다 나쁨</span>
				{/if}
			</div>
			<div class="metric-item">
				<span class="metric-label">검증 표본 수</span>
				<span class="metric-value">{$prediction.metrics.n_val ?? '-'}</span>
			</div>
		</div>

		{#if baseClose > 0}
			<p class="predict-base">기준 {baseDate} 종가 {won(baseClose)}원 — 첫날 등락은 이 종가 대비</p>
		{/if}

		<table class="predict-table">
			<thead>
				<tr>
					<th>날짜</th>
					<th>예측 종가</th>
					<th>전일대비</th>
					<th>기준 대비</th>
				</tr>
			</thead>
			<tbody>
				{#each $prediction.predictions as point, i}
					{@const base = i === 0 ? baseClose : $prediction.predictions[i - 1].close}
					{@const d = diff(point.close, base)}
					<tr>
						<td>{point.date}</td>
						<td>{won(point.close)}</td>
						<!-- 기준가가 없으면 등락을 계산하지 않음 (현재가로 대신하지 않음) -->
						{#if base > 0}
							<td class:td-up={d.value > 0} class:td-down={d.value < 0}>
								{d.value > 0 ? '+' : ''}{won(d.value)}
								({d.pct > 0 ? '+' : ''}{d.pct.toFixed(2)}%)
							</td>
						{:else}
							<td>-</td>
						{/if}
						<!-- 기준 종가 대비 누적 등락률 (서버 계산값) -->
						<td class:td-up={point.change_pct > 0} class:td-down={point.change_pct < 0}>
							{pctlabel(point.change_pct)}
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
