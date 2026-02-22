<script lang="ts">
	import '../app.css';

	let { children } = $props();
</script>

<svelte:head>
	<title>KI AutoTrade</title>
	<meta name="description" content="Korean Stock Auto-Trading Dashboard" />
</svelte:head>

{@render children()}
