import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { Slide } from '../../types/slide';
import { useState } from 'react';

interface SortableListItemProps {
    slide: Slide;
    index: number;
    selectedSlide: number;
    onSlideClick: (index: any) => void;
}

export function SortableListItem({ slide, index, selectedSlide, onSlideClick }: SortableListItemProps) {
    const [mouseDownTime, setMouseDownTime] = useState(0);

    const {
        attributes,
        listeners,
        setNodeRef,
        transform,
        transition,
        isDragging
    } = useSortable({ id: slide.id! });

    const style = {
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.5 : 1
    };

    const handleMouseDown = () => {
        setMouseDownTime(Date.now());
    };

    const handleMouseUp = () => {
        const mouseUpTime = Date.now();
        const timeDiff = mouseUpTime - mouseDownTime;

        // If the mouse was down for less than 200ms, consider it a click
        if (timeDiff < 200 && !isDragging) {
            onSlideClick(slide.index);
        }
    };

    return (
        <div
            ref={setNodeRef}
            style={style}
            {...attributes}
            {...listeners}
            onMouseDown={handleMouseDown}
            onMouseUp={handleMouseUp}
            className={`p-3 cursor-pointer ring-0 border-[3px] rounded-lg slide-box
                ${selectedSlide === index
                    ? ' border-[#5141e5] '
                    : 'hover:slide-box/40 border-gray-300'
                }`}
        >
            <span className="font-medium slide-title">Slide {index + 1}</span>
            <p className="text-sm slide-description">
                {slide.content.title}
            </p>
        </div>
    );
} 