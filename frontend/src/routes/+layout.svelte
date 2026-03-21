<script lang="ts">
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
