<script lang="ts">
	// 루트 레이아웃 — 전역 스타일 + 토스트 컨테이너
	import '../app.css';
	import Toast from '$lib/components/toast/Toast.svelte';

	let { children } = $props();
</script>

<svelte:head>
	<title>KI AutoTrade</title>
	<meta name="description" content="Korean Stock Auto-Trading Dashboard" />
</svelte:head>

{@render children()}
<Toast />
