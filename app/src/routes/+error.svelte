<script lang="ts">
	import { page } from '$app/state';
</script>

<div class="flex min-h-screen items-center justify-center bg-bg px-6">
	<div class="max-w-sm text-center">
		<p class="text-5xl font-semibold text-text-dim">{page.status}</p>
		<p class="mt-3 text-text-muted">
			{page.error?.message ?? 'Something went wrong.'}
		</p>
		<a href="/" class="mt-6 inline-block text-sm text-accent hover:text-accent-hover transition-colors">
			Back to dashboard
		</a>
	</div>
</div>
