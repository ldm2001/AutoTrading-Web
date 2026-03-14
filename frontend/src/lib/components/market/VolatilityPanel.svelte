<script lang="ts">
	let { code, name }: { code: string; name: string } = $props();

	interface VolData {
		atr: number | null;
		atr_pct: number | null;
		bb_width: number | null;
		daily_range_pct: number | null;
		volatility_grade: string;
		rsi: number | null;
		bb_position?: number;
		bb?: { upper: number; middle: number; lower: number; current_price: number };
	}

	let data = $state<VolData | null>(null);
	let loading = $state(true);

	async function load() {
		loading = true;
		try {
			const res = await fetch(`/api/stocks/${code}/volatility`);
			data = await res.json();
		} catch {
			data = null;
		}
		loading = false;
	}

	$effect(() => { code; load(); });

	const gradeColor = (g: string) => {
		if (g === '매우높음') return '#dc2626';
		if (g === '높음') return '#ea580c';
		if (g === '보통') return '#d97706';
		return '#059669';
	};

	const rsiZone = (v: number | null) => {
		if (v == null) return { label: '-', color: '#9ca3af' };
		if (v >= 70) return { label: '과매수', color: '#dc2626' };
		if (v <= 30) return { label: '과매도', color: '#2563eb' };
		return { label: '중립', color: '#059669' };
	};
</script>

<div class="vp-wrap">
	{#if loading}
		<p class="vp-msg">불러오는 중...</p>
	{:else if !data}
		<p class="vp-msg">데이터를 불러올 수 없습니다</p>
	{:else}
		<div class="vp-hero">
			<span class="vp-hero-label">변동성 등급</span>
			<span class="vp-hero-grade" style="color: {gradeColor(data.volatility_grade)}">
				{data.volatility_grade}
			</span>
		</div>

		<div class="vp-grid">
			<div class="vp-card">
				<span class="vp-label">ATR (14)</span>
				<strong>{data.atr?.toLocaleString() ?? '-'}</strong>
				{#if data.atr_pct != null}
					<span class="vp-sub">{data.atr_pct}% of price</span>
				{/if}
			</div>
			<div class="vp-card">
				<span class="vp-label">BB 밴드폭</span>
				<strong>{data.bb_width != null ? `${data.bb_width}%` : '-'}</strong>
				<span class="vp-sub">밴드 넓을수록 변동성 큼</span>
			</div>
			<div class="vp-card">
				<span class="vp-label">일중 변동폭</span>
				<strong>{data.daily_range_pct != null ? `${data.daily_range_pct}%` : '-'}</strong>
				<span class="vp-sub">최근 20일 평균</span>
			</div>
			<div class="vp-card">
				<span class="vp-label">RSI (14)</span>
				<strong>{data.rsi ?? '-'}</strong>
				<span class="vp-sub" style="color: {rsiZone(data.rsi).color}">{rsiZone(data.rsi).label}</span>
			</div>
		</div>

		{#if data.bb}
			<div class="vp-bb">
				<span class="vp-section-title">볼린저밴드 위치</span>
				<div class="vp-bb-track">
					<div class="vp-bb-marker" style="left: {data.bb_position ?? 50}%"></div>
					<span class="vp-bb-low">{data.bb.lower.toLocaleString()}</span>
					<span class="vp-bb-mid">{data.bb.middle.toLocaleString()}</span>
					<span class="vp-bb-high">{data.bb.upper.toLocaleString()}</span>
				</div>
				<div class="vp-bb-labels">
					<span>하단</span>
					<span>중심</span>
					<span>상단</span>
				</div>
			</div>
		{/if}
	{/if}
</div>

<style>
	.vp-wrap { display: flex; flex-direction: column; gap: 0.875rem; }
	.vp-msg { text-align: center; color: #9ca3af; font-size: 0.8125rem; padding: 2rem 0; }

	.vp-hero { text-align: center; padding: 0.75rem 0; }
	.vp-hero-label { display: block; font-size: 0.625rem; font-weight: 700; color: #9ca3af; letter-spacing: 0.06em; margin-bottom: 0.25rem; }
	.vp-hero-grade { font-size: 1.25rem; font-weight: 800; }

	.vp-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 0.5rem; }
	.vp-card { display: flex; flex-direction: column; gap: 0.125rem; padding: 0.625rem 0.75rem; border: 1px solid #e5e7eb; border-radius: 0.375rem; background: #f9fafb; }
	.vp-label { font-size: 0.5625rem; font-weight: 700; color: #9ca3af; letter-spacing: 0.04em; }
	.vp-card strong { font-size: 0.9375rem; font-weight: 700; color: #111827; font-family: 'SF Mono', 'Consolas', monospace; }
	.vp-sub { font-size: 0.5625rem; color: #9ca3af; }

	.vp-section-title { font-size: 0.6875rem; font-weight: 700; color: #374151; }

	.vp-bb { display: flex; flex-direction: column; gap: 0.375rem; }
	.vp-bb-track { position: relative; height: 0.75rem; background: linear-gradient(90deg, rgba(59,130,246,0.15), rgba(107,114,128,0.08) 50%, rgba(239,68,68,0.15)); border-radius: 999px; }
	.vp-bb-marker { position: absolute; top: -0.125rem; width: 1rem; height: 1rem; background: #111827; border: 2px solid #fff; border-radius: 50%; transform: translateX(-50%); box-shadow: 0 1px 4px rgba(0,0,0,0.15); }
	.vp-bb-low, .vp-bb-mid, .vp-bb-high { position: absolute; top: 1rem; font-size: 0.5625rem; color: #9ca3af; font-family: 'SF Mono', 'Consolas', monospace; }
	.vp-bb-low { left: 0; }
	.vp-bb-mid { left: 50%; transform: translateX(-50%); }
	.vp-bb-high { right: 0; }

	.vp-bb-labels { display: flex; justify-content: space-between; font-size: 0.5625rem; color: #9ca3af; margin-top: 0.75rem; }
</style>
