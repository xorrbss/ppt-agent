import React, { memo } from "react";

const SlideContent = memo(({ slide }: { slide: any }) => {
  return (
    <div
      dangerouslySetInnerHTML={{
        __html: slide.html,
      }}
    />
  );
});

export default SlideContent;
