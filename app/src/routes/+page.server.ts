import { addCacheTag } from '@vercel/functions';
import { fetchData } from '$lib/api.js';
import type { PageServerLoad } from './$types.js';

export const config = { isr: { expiration: 3600 } };

export const load: PageServerLoad = async ({ fetch }) => {
	addCacheTag('dashboard');
	return await fetchData(fetch);
};
