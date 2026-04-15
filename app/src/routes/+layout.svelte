<script lang="ts">
	import '../app.css';

	let { children } = $props();
</script>

<svelte:head>
	<title>cc-sentiment</title>
	<meta name="description" content="Live developer sentiment from real Claude Code sessions, scored on-device." />
	<meta property="og:site_name" content="cc-sentiment" />
	<meta property="og:type" content="website" />
	<meta name="twitter:card" content="summary_large_image" />
</svelte:head>

{@render children()}
