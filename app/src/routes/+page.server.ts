import { addCacheTag } from '@vercel/functions';
import { fetchData } from '$lib/api.js';
import type { PageServerLoad } from './$types.js';

export const config = { isr: { expiration: 3600 } };

export const load: PageServerLoad = async ({ fetch, url }) => {
	addCacheTag('dashboard');
	const u = url.searchParams.get('u');
	const t = url.searchParams.get('t');
	const share = u && t ? { u, t } : null;
	return { ...(await fetchData(fetch)), share };
};
