<script lang="ts">
	import { priceConnected, tradeConnected } from '$lib/stores/websocket';
	import { tradingStatus, startBot, stopBot } from '$lib/stores/trading';
	import './Header.css';

	let { onmenu }: { onmenu?: () => void } = $props();

	let now = $state(new Date());
	let switching = $state(false);

	$effect(() => {
		const timer = setInterval(() => (now = new Date()), 1000);
		return () => clearInterval(timer);
	});

	const timeStr = $derived(
		now.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
	);

	async function toggleBot() {
		switching = true;
		if ($tradingStatus.is_running) {
			await stopBot();
		} else {
			await startBot();
		}
		switching = false;
	}
</script>

<header class="header">
	<div class="header-left">
		<h1 class="header-title">KI AutoTrade</h1>
		<p class="header-subtitle">Presented by Lee Dong Min</p>
	</div>

	<div class="header-right">
		<span class="header-time">{timeStr}</span>

		<div class="status-group">
			<span class="status-item">
				<span class="status-dot" class:on={$priceConnected} class:off={!$priceConnected}></span>
				<span class="status-label">Price</span>
			</span>
			<span class="status-item">
				<span class="status-dot" class:on={$tradeConnected} class:off={!$tradeConnected}></span>
				<span class="status-label">Trade</span>
			</span>
		</div>

		<!-- Trading ON/OFF Toggle -->
		<button
			class="trading-toggle"
			class:active={$tradingStatus.is_running}
			onclick={toggleBot}
			disabled={switching}
		>
			<span class="toggle-track">
				<span class="toggle-thumb"></span>
			</span>
			<span class="toggle-copy">
				<span class="toggle-meta">전체 자동매매</span>
				<span class="toggle-label">{$tradingStatus.is_running ? 'ON' : 'OFF'}</span>
			</span>
		</button>

		<button class="menu-btn" type="button" onclick={onmenu} aria-label="메뉴 열기">
			<svg width="18" height="18" viewBox="0 0 18 18" fill="none">
				<path d="M3 4.5h12M3 9h12M3 13.5h12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
			</svg>
		</button>
	</div>
</header>
