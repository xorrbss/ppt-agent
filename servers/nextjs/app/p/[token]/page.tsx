import PublicPresentationView from "./PublicPresentationView";

// Public, unauthenticated read-only viewer. Lives outside the (presentation-generator)
// segment (which requires the admin session) and outside the proxy matcher, so a
// share link works without logging in. Access is gated by the unguessable token.
export default async function PublicPresentationPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;
  return <PublicPresentationView token={token} />;
}
