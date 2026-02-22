<script lang="ts">
	import { onMount } from 'svelte';
	import {
		createChart,
		LineSeries,
		HistogramSeries,
		type IChartApi
	} from 'lightweight-charts';
	import { selectedStock, dailyCandles, fetchDailyCandles, stockMap } from '$lib/stores/stocks';
	import { prediction } from '$lib/stores/predict';
	import type { DailyCandle } from '$lib/types';
	import './StockChart.css';

	const UP_COLOR = '#ef4444';   // 상승 - 빨강
	const DOWN_COLOR = '#2563eb'; // 하락 - 파랑
	const FLAT_COLOR = '#9ca3af'; // 보합

	let container: HTMLDivElement;
	let chart: IChartApi;
	let mainSeries: ReturnType<typeof chart.addSeries>;
	let volumeSeries: ReturnType<typeof chart.addSeries> | null = null;
	let predLine: ReturnType<typeof chart.addSeries> | null = null;

	const stockInfo = $derived($stockMap.get($selectedStock));

	const latestCandle = $derived(() => {
		const c = $dailyCandles;
		return c.length > 0 ? c[c.length - 1] : null;
	});

	const highestPrice = $derived(() => {
		const c = $dailyCandles;
		if (c.length === 0) return 0;
		return Math.max(...c.map(x => x.high));
	});

	const lowestPrice = $derived(() => {
		const c = $dailyCandles;
		if (c.length === 0) return 0;
		return Math.min(...c.map(x => x.low));
	});

	// 캔들 배열 → 상승 빨강 / 하락 파랑 per-point 라인 데이터
	function toColoredLine(candles: DailyCandle[]) {
		return candles.map((c, i) => ({
			time: c.date,
			value: c.close,
			color: i === 0
				? FLAT_COLOR
				: c.close > candles[i - 1].close
					? UP_COLOR
					: c.close < candles[i - 1].close
						? DOWN_COLOR
						: FLAT_COLOR,
		}));
	}

	function buildChart() {
		if (!chart || $dailyCandles.length === 0) return;

		if (mainSeries) { try { chart.removeSeries(mainSeries); } catch {} }
		if (volumeSeries) { try { chart.removeSeries(volumeSeries); } catch {} volumeSeries = null; }
		if (predLine) { try { chart.removeSeries(predLine); } catch {} predLine = null; }

		const candles = $dailyCandles;
		const lastDir = candles.length > 1
			? (candles[candles.length - 1].close >= candles[candles.length - 2].close ? UP_COLOR : DOWN_COLOR)
			: FLAT_COLOR;

		// 주가 라인 - 상승/하락 per-point 색상
		mainSeries = chart.addSeries(LineSeries, {
			lineWidth: 2.5,
			lineType: 2,
			crosshairMarkerVisible: true,
			crosshairMarkerRadius: 4,
			crosshairMarkerBackgroundColor: lastDir,
			crosshairMarkerBorderColor: '#ffffff',
			crosshairMarkerBorderWidth: 2,
			lastValueVisible: true,
			priceLineVisible: true,
			priceLineColor: lastDir,
			priceLineStyle: 2,
			priceLineWidth: 1,
		});
		mainSeries.setData(toColoredLine(candles) as any);

		// 거래량 - 상승일 빨강, 하락일 파랑
		volumeSeries = chart.addSeries(HistogramSeries, {
			priceFormat: { type: 'volume' },
			priceScaleId: 'volume',
		});
		chart.priceScale('volume').applyOptions({
			scaleMargins: { top: 0.85, bottom: 0 },
		});
		volumeSeries.setData(candles.map((c) => ({
			time: c.date,
			value: c.volume,
			color: c.close > c.open
				? 'rgba(239, 68, 68, 0.35)'
				: c.close < c.open
					? 'rgba(37, 99, 235, 0.35)'
					: 'rgba(156, 163, 175, 0.25)',
		})) as any);

		// AI 예측 오버레이 (초록)
		const pred = $prediction;
		if (pred && pred.predictions.length > 0 && pred.code === $selectedStock) {
			const lastCandle = candles[candles.length - 1];
			const lineData = [
				{ time: lastCandle.date, value: lastCandle.close },
				...pred.predictions.map((p) => ({ time: p.date, value: p.close })),
			];
			predLine = chart.addSeries(LineSeries, {
				color: '#16a34a',
				lineWidth: 2,
				lineStyle: 2,
				lineType: 2,
				pointMarkersVisible: true,
				pointMarkersRadius: 3,
			});
			predLine.setData(lineData as any);
		}

		chart.timeScale().fitContent();
	}

	onMount(() => {
		chart = createChart(container, {
			layout: {
				background: { color: '#ffffff' },
				textColor: '#4b5563',
				fontFamily: "'SF Mono', 'Cascadia Code', 'Consolas', monospace",
				attributionLogo: false,
			},
			grid: {
				vertLines: { color: '#e5e7eb', style: 1 },
				horzLines: { color: '#e5e7eb', style: 1 },
			},
			autoSize: true,
			timeScale: {
				timeVisible: false,
				borderColor: '#d1d5db',
				rightOffset: 5,
			},
			rightPriceScale: {
				borderColor: '#d1d5db',
			},
			crosshair: {
				mode: 0,
				vertLine: { color: '#9ca3af', width: 1, style: 2 },
				horzLine: { color: '#9ca3af', width: 1, style: 2 },
			},
		});

		mainSeries = chart.addSeries(LineSeries, {
			lineWidth: 2.5,
			lineType: 2,
		});

		fetchDailyCandles($selectedStock);

		return () => chart.remove();
	});

	// 종목 변경 시 캔들 새로 로드
	$effect(() => {
		if (mainSeries) fetchDailyCandles($selectedStock);
	});

	// 캔들 데이터 변경 시 차트 재빌드
	$effect(() => {
		if (mainSeries && $dailyCandles.length > 0) buildChart();
	});

	// 실시간 가격 업데이트 - 오늘 캔들 색상·가격 반영
	$effect(() => {
		const info = stockInfo;
		if (!mainSeries || !info || $dailyCandles.length === 0) return;

		const prevClose = $dailyCandles.length > 1
			? $dailyCandles[$dailyCandles.length - 2].close
			: info.price;
		const color = info.price > prevClose ? UP_COLOR : info.price < prevClose ? DOWN_COLOR : FLAT_COLOR;
		const lastDate = $dailyCandles[$dailyCandles.length - 1].date;

		try {
			mainSeries.update({ time: lastDate, value: info.price, color } as any);
			mainSeries.applyOptions({
				priceLineColor: color,
				crosshairMarkerBackgroundColor: color,
			});
		} catch {}
	});
</script>

<div class="chart-wrap">
	<div class="chart-header">
		<div>
			<h3 class="chart-title">{stockInfo?.name ?? $selectedStock}</h3>
			<span class="chart-subtitle">{$selectedStock} | 일봉 차트</span>
		</div>

		<div class="chart-legend">
			<span class="legend-item">
				<span class="legend-seg up"></span><span class="legend-seg down"></span>
				상승/하락
			</span>
			<span class="legend-item"><span class="legend-bar"></span>거래량</span>
			{#if $prediction && $prediction.code === $selectedStock}
				<span class="legend-item"><span class="legend-line green"></span>AI 예측</span>
			{/if}
		</div>

		{#if stockInfo}
			<div style="text-align:right">
				<div class="chart-price">{stockInfo.price.toLocaleString('ko-KR')}</div>
				<div
					class="chart-change"
					class:up={stockInfo.change > 0}
					class:down={stockInfo.change < 0}
					class:flat={stockInfo.change === 0}
				>
					{stockInfo.change > 0 ? '+' : ''}{stockInfo.change.toLocaleString()}
					({stockInfo.change_percent > 0 ? '+' : ''}{stockInfo.change_percent.toFixed(2)}%)
				</div>
			</div>
		{/if}
	</div>

	<div bind:this={container} class="chart-container"></div>

	<!-- 기업 상세 정보 -->
	{#if stockInfo && latestCandle()}
		<div class="stock-details">
			<div class="detail-item">
				<span class="detail-label">시가</span>
				<span class="detail-value">{latestCandle()!.open.toLocaleString()}</span>
			</div>
			<div class="detail-item">
				<span class="detail-label">최고</span>
				<span class="detail-value up">{latestCandle()!.high.toLocaleString()}</span>
			</div>
			<div class="detail-item">
				<span class="detail-label">최저</span>
				<span class="detail-value down">{latestCandle()!.low.toLocaleString()}</span>
			</div>
			<div class="detail-item">
				<span class="detail-label">시가총액</span>
				<span class="detail-value">{stockInfo.market_cap}</span>
			</div>
			<div class="detail-item">
				<span class="detail-label">거래량</span>
				<span class="detail-value">{stockInfo.volume.toLocaleString()}</span>
			</div>
			<div class="detail-item">
				<span class="detail-label">기간 최고</span>
				<span class="detail-value up">{highestPrice().toLocaleString()}</span>
			</div>
			<div class="detail-item">
				<span class="detail-label">기간 최저</span>
				<span class="detail-value down">{lowestPrice().toLocaleString()}</span>
			</div>
		</div>
	{/if}
</div>
