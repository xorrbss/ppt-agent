"use client";
import React, { useEffect, useState } from "react";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Input } from "@/components/ui/input";
import { PlusIcon, Search } from "lucide-react";
import { cn } from "@/lib/utils";
import { useDispatch, useSelector } from "react-redux";
import { PresentationGenerationApi } from "../services/api/presentation-generation";
import { RootState } from "@/store/store";
import { usePathname, useSearchParams } from "next/navigation";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { updateSlideIcon } from "@/store/slices/presentationGeneration";
import { getStaticFileUrl } from "../utils/others";

interface IconsEditorProps {
  icon: string;
  index: number;
  backgroundColor: string;
  hasBg: boolean;
  slideIndex: number;
  elementId: string;
  isWhite?: boolean;
  className?: string;
  icon_prompt?: string[] | null;
  onClose?: () => void;
}

const IconsEditor = ({
  icon: initialIcon,
  index,
  backgroundColor,
  hasBg,
  className,
  slideIndex,
  elementId,
  icon_prompt,
  onClose,
}: IconsEditorProps) => {
  const dispatch = useDispatch();

  const [icon, setIcon] = useState(initialIcon);
  const [icons, setIcons] = useState<string[]>([]);
  const [isEditorOpen, setIsEditorOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState<string>(
    icon_prompt?.[0] || ""
  );
  const [loading, setLoading] = useState(true);
  const searchParams = useSearchParams();

  useEffect(() => {
    setIcon(initialIcon);
  }, [initialIcon]);

  useEffect(() => {
    if (isEditorOpen) {
      handleIconSearch();
    }
  }, [isEditorOpen]);

  const handleIconClick = () => {
    setIsEditorOpen(true);
  };

  const handleIconSearch = async () => {
    setLoading(true);
    const presentation_id = searchParams.get("id");
    const query = searchQuery.length > 0 ? searchQuery : icon_prompt?.[0] || "";

    try {
      const data = await PresentationGenerationApi.searchIcons({
        presentation_id: presentation_id!,
        query,
        page: 1,
        limit: 40,
      });
      setIcons(data.paths);
    } catch (error) {
      console.error("Error fetching icons:", error);
      setIcons([]);
    } finally {
      setLoading(false);
    }
  };

  const handleIconChange = (newIcon: string) => {


    setIcon(newIcon);
    dispatch(
      updateSlideIcon({ index: slideIndex, iconIdx: index, icon: newIcon })
    );
    setIsEditorOpen(false);
  };

  return (
    <Sheet open={true} onOpenChange={() => onClose?.()}>
      <SheetContent
        side="right"
        className="w-[400px]"
        onOpenAutoFocus={(e) => e.preventDefault()}
        onClick={(e) => e.stopPropagation()}
      >
        <SheetHeader>
          <SheetTitle>Choose Icon</SheetTitle>
        </SheetHeader>
        <div className="mt-6 space-y-4">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              e.stopPropagation();
              handleIconSearch();
            }}
          >
            <div className="relative mb-3">
              <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-gray-500 w-4 h-4" />

              <Input
                placeholder="Search icons..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onClick={(e) => e.stopPropagation()}
                className="pl-10"
              />
            </div>
            <Button
              type="submit"
              variant="outline"
              className="w-full text-semibold text-[#51459e]"
              onClick={(e) => e.stopPropagation()}
            >
              Search
            </Button>
          </form>

          {/* Icons grid */}
          <div className="max-h-[80vh] hide-scrollbar overflow-y-auto p-1">
            {loading ? (
              <div className="grid grid-cols-4 gap-4">
                {Array.from({ length: 40 }).map((_, index) => (
                  <Skeleton key={index} className="w-16 h-16 rounded-lg" />
                ))}
              </div>
            ) : icons.length > 0 ? (
              <div className="grid grid-cols-4 gap-4">
                {icons.map((iconSrc, idx) => (
                  <div
                    key={idx}
                    onClick={(e) => {
                      e.stopPropagation();
                      handleIconChange(iconSrc);
                    }}
                    className="w-12 h-12 cursor-pointer group relative rounded-lg overflow-hidden hover:bg-gray-100 p-2"
                  >
                    <img
                      src={getStaticFileUrl(iconSrc)}
                      alt={`Icon ${idx + 1}`}
                      className="w-full h-full object-contain "
                    />
                  </div>
                ))}
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center w-full h-[60vh] text-center text-gray-500 space-y-4">
                <Search className="w-12 h-12 text-gray-400" />
                <p className="text-sm">No icons found for your search.</p>
                <p className="text-xs">Try refining your search query.</p>
              </div>
            )}
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
};

export default IconsEditor;
