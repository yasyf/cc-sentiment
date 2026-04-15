import { redirect } from '@sveltejs/kit';
import type { RequestHandler } from './$types.js';

const TARBALL_URL =
	'https://github.com/yasyf/cc-sentiment/releases/latest/download/cc-sentiment.tar.gz';

export const GET: RequestHandler = () => {
	redirect(302, TARBALL_URL);
};
