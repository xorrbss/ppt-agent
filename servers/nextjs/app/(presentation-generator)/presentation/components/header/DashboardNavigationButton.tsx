import Image from "next/image";

export default function DashboardNavigationButton({
  onNavigate,
}: {
  onNavigate: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onNavigate}
      aria-label="대시보드로 이동"
      className="shrink-0 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5141e5] focus-visible:ring-offset-2"
    >
      <Image
        src="/logo-with-bg.png"
        alt=""
        width={40}
        height={40}
        className="h-10 w-10 object-contain"
      />
    </button>
  );
}
