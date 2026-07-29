import { handleAuth } from "@workos-inc/authkit-nextjs";

export const GET = handleAuth({
  returnPathname: "/",
  baseURL: process.env.WORKOS_REDIRECT_URI?.replace(/\/auth\/callback$/, ""),
});
