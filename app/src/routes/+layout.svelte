<script lang="ts">
	import '../app.css';
	import { ModeWatcher } from 'mode-watcher';

	let { children } = $props();
</script>

<svelte:head>
	<title>cc-sentiment</title>
	<meta name="description" content="Live developer sentiment from real Claude Code sessions, scored on-device." />
	<meta property="og:site_name" content="cc-sentiment" />
	<meta property="og:type" content="website" />
	<meta name="twitter:card" content="summary_large_image" />
</svelte:head>

<ModeWatcher
	defaultMode="system"
	themeColors={{ light: '#fafafa', dark: '#09090b' }}
	disableTransitions
/>

{@render children()}
