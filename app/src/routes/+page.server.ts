import { fetchData } from '$lib/api.js';
import type { PageServerLoad } from './$types.js';

export const load: PageServerLoad = async () => {
	return await fetchData();
};
