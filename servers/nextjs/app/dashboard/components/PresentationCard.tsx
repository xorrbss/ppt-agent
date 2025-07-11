import React from "react";

import { Card } from "@/components/ui/card";
import { DashboardApi } from "../api/dashboard";
import { DotsVerticalIcon, TrashIcon } from "@radix-ui/react-icons";
import {
  Popover,
  PopoverTrigger,
  PopoverContent,
} from "@/components/ui/popover";
import { useRouter } from "next/navigation";
import { toast } from "@/hooks/use-toast";
import { renderSlideContent } from "@/app/(presentation-generator)/components/slide_config";

export const PresentationCard = ({
  id,
  title,
  created_at,
  thumbnail,
  theme,
}: {
  id: string;
  title: string;
  created_at: string;
  thumbnail: string;
  theme: any;
}) => {
  const router = useRouter();

  const handlePreview = (e: React.MouseEvent) => {
    e.preventDefault();
    router.push(`/presentation?id=${id}`);
  };

  const handleDelete = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();

    toast({
      title: "Deleting presentation",
      description: "Please wait while we delete the presentation",
      variant: "default",
    });
    const response = await DashboardApi.deletePresentation(id);

    if (response) {
      toast({
        title: "Presentation deleted",
        description: "The presentation has been deleted successfully",
        variant: "default",
      });
    } else {
      toast({
        title: "Error",
        description: "Failed to delete presentation",
        variant: "destructive",
      });
    }
    window.location.reload();
  };

  const themeName = theme.name;
  // Create CSS variables object
  const cssVariables = {
    '--slide-bg': theme.colors.slideBg,
    '--slide-title': theme.colors.slideTitle,
    '--slide-heading': theme.colors.slideHeading,
    '--slide-description': theme.colors.slideDescription,
    '--slide-box': theme.colors.slideBox,
    '--icon-bg': theme.colors.iconBg,
    '--background': theme.colors.background,
    '--font-family': theme.colors.fontFamily,
  } as React.CSSProperties;

  return (
    <Card
      onClick={handlePreview}
      data-theme={themeName}
      className="bg-white rounded-[8px] slide-theme cursor-pointer overflow-hidden p-4"
      style={cssVariables}
    >
      <div className="space-y-4">
        {/* Date */}
        <div className="flex items-center justify-between">
          <p className="text-[#667085] text-sm font-roboto pt-2">
            {new Date(created_at).toLocaleDateString()}
          </p>
          <Popover>
            <PopoverTrigger onClick={(e) => e.stopPropagation()}>
              <DotsVerticalIcon className="w-4 h-4 text-gray-500" />
            </PopoverTrigger>
            <PopoverContent align="end" className="bg-white w-[200px]">
              <button
                className="flex items-center justify-between w-full px-2 py-1 hover:bg-gray-100"
                onClick={handleDelete}
              >
                <p>Delete</p>
                <TrashIcon className="w-4 h-4 text-red-500" />
              </button>
            </PopoverContent>
          </Popover>
        </div>

        {/* Thumbnail */}
        {/* <div className="relative border-2 border-gray-200 aspect-[16/9] rounded-[8px] overflow-hidden">
          {thumbnail ? (
            <img
              src={getStaticFileUrl(thumbnail)}
              alt={title}
              className="object-cover h-full w-full"
            />
          ) : (
            <div className="flex flex-col items-center justify-center h-full w-full">
              <p className="text-gray-500 text-sm font-roboto">
                No thumbnail yet
              </p>
              <p className="text-gray-500 text-sm font-roboto">
                Will be added shortly
              </p>
            </div>
          )}
        </div> */}
        <div className=" slide-box relative overflow-hidden border aspect-video"
          style={{

          }}
        >
          <div className="absolute bg-transparent z-40 top-0 left-0 w-full h-full" />
          <div className="transform scale-[0.2] flex justify-center items-center origin-top-left  w-[500%] h-[500%]">
            {renderSlideContent({
              id: 'mock-slide-1',
              type: 1,
              index: 0,
              design_index: 1,
              properties: null,
              images: ['/static/user_data/ee7cb066-86d0-45fc-adc9-15bf565eab30/images/af54ed41-483e-4983-aef0-b254aac48408.jpg'],
              icons: [],
              graph_id: null,
              presentation: id,
              content: {
                title: title || 'Sample Presentation',
                body: "This is a sample slide description to demonstrate the layout and styling. The content here helps visualize how actual presentation content would appear.",
                infographics: [],
                image_prompts: ['Sample image showing business growth']
              },
            }, 'English')}
          </div>
        </div>

        {/* Icon and Title */}
        <div className="flex items-center gap-2 pb-1">
          <svg
            xmlns="http://www.w3.org/2000/svg"
            className="w-full h-full max-w-[20px] max-h-[20px]"
            viewBox="0 0 24 24"
            fill="none"
          >
            <path
              d="M15.75 0.75V6C15.75 6.42 16.08 6.75 16.5 6.75H21.75M9.75 17.25H7.5C7.08 17.25 6.75 16.92 6.75 16.5V12C6.75 11.58 7.08 11.25 7.5 11.25H13.5C13.92 11.25 14.25 11.58 14.25 12V14.25M21.75 6.3V22.5C21.75 22.92 21.42 23.25 21 23.25H3C2.58 23.25 2.25 22.92 2.25 22.5V1.5C2.25 1.08 2.58 0.75 3 0.75H16.275C16.47 0.75 16.665 0.825 16.815 0.975L21.54 5.775C21.675 5.925 21.75 6.105 21.75 6.3ZM10.5 14.25H16.5C16.92 14.25 17.25 14.58 17.25 15V19.5C17.25 19.92 16.92 20.25 16.5 20.25H10.5C10.08 20.25 9.75 19.92 9.75 19.5V15C9.75 14.58 10.08 14.25 10.5 14.25Z"
              stroke="black"
              strokeWidth="1.5"
            />
          </svg>
          <p className="text-[#667085] text-sm ml-1 line-clamp-2 font-roboto">
            {title}
          </p>
        </div>
      </div>
    </Card>
  );
};
