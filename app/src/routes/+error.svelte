<script lang="ts">
	import { page } from '$app/state';
</script>

<div class="flex min-h-screen items-center justify-center bg-bg px-6">
	<div class="max-w-md text-center">
		<p class="text-6xl font-bold text-accent">{page.status}</p>
		<h1 class="mt-4 text-xl font-semibold text-text">
			{#if page.status === 500}
				Something went wrong
			{:else if page.status === 404}
				Page not found
			{:else}
				Error
			{/if}
		</h1>
		<p class="mt-2 text-sm text-text-muted">
			{page.error?.message ?? 'An unexpected error occurred.'}
		</p>
		<a href="/" class="mt-6 inline-block rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover transition-colors">
			Back to dashboard
		</a>
	</div>
</div>
