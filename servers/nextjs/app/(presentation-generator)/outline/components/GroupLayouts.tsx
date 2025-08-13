import { CheckCircle } from "lucide-react";
import React from "react";
import { usePathname } from "next/navigation";
import { trackEvent, MixpanelEvent } from "@/utils/mixpanel";
import { LayoutGroup } from "../types/index";
import { useLayout } from "../../context/LayoutContext";
import { useFontLoader } from "../../hooks/useFontLoader";
interface GroupLayoutsProps {
  group: LayoutGroup;
  onSelectLayoutGroup: (group: LayoutGroup) => void;
  selectedLayoutGroup: LayoutGroup | null;
}

const GroupLayouts: React.FC<GroupLayoutsProps> = ({
  group,
  onSelectLayoutGroup,
  selectedLayoutGroup,
}) => {
  const { getFullDataByGroup,getCustomTemplateFonts } = useLayout();
  const layoutGroup = getFullDataByGroup(group.id);
  const fonts = getCustomTemplateFonts(group.id.split("custom-")[1]);
  useFontLoader(fonts || []);
  const pathname = usePathname();
  return (
    <div
      onClick={() => {
        trackEvent(MixpanelEvent.Group_Layout_Selected_Clicked, { pathname });
        onSelectLayoutGroup(group);
      }}
      className={`relative p-4 rounded-lg border cursor-pointer transition-all duration-200 ${
        selectedLayoutGroup?.id === group.id
          ? "border-blue-500 bg-blue-50 shadow-md"
          : "border-gray-200 bg-white hover:border-gray-300 hover:shadow-sm"
      }`}
    >
      {selectedLayoutGroup?.id === group.id && (
        <div className="absolute top-3 right-3">
          <CheckCircle className="w-5 h-5 text-blue-500" />
        </div>
      )}

      <div className="mb-3 ">
        <h6 className="text-base capitalize font-medium text-gray-900 mb-1">
          {group.name}
        </h6>
        <p className="text-sm text-gray-600">{group.description}</p>
      </div>

      {/* Layout previews */}
      <div className="grid grid-cols-2 gap-2 mb-3 min-h-[300px]">
        {layoutGroup &&
          layoutGroup?.slice(0, 4).map((layout: any, index: number) => {
            const {
              component: LayoutComponent,
              sampleData,
              layoutId,
              groupName,
            } = layout;
            return (
              <div
                key={`${groupName}-${index}`}
                className=" relative cursor-pointer overflow-hidden aspect-video"
              >
                <div className="absolute cursor-pointer bg-transparent z-40 top-0 left-0 w-full h-full" />
                <div className="transform scale-[0.2] flex justify-center items-center origin-top-left  w-[500%] h-[500%]">
                  <LayoutComponent data={sampleData} />
                </div>
              </div>
            );
          })}
      </div>

      <div className="flex items-center justify-between text-sm text-gray-500">
        <span>{layoutGroup?.length} layouts</span>
        <span
          className={`px-2 py-1 rounded text-xs ${
            group.ordered
              ? "bg-gray-100 text-gray-700"
              : "bg-blue-100 text-blue-700"
          }`}
        >
          {group.ordered ? "Structured" : "Flexible"}
        </span>
      </div>
    </div>
  );
};

export default GroupLayouts;
