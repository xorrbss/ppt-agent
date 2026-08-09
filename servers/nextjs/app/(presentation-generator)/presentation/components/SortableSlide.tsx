import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { useTranslations } from 'next-intl';
import type { Slide } from '../../types/slide';
import { useRef, type PointerEventHandler } from 'react';
import { SlideThumbnailCard } from './SlideThumbnailCard';
interface SortableSlideProps {
    slide: Slide;
    index: number;
    selectedSlide: number;
    onSlideClick: (index: any) => void;
    previewScale?: number;
}

export function SortableSlide({ slide, index, selectedSlide, onSlideClick, previewScale }: SortableSlideProps) {
    const t = useTranslations('slideNavigation');
    const lastClickTime = useRef(0);
    const {
        attributes,
        listeners,
        setNodeRef,
        transform,
        transition,
        isDragging,
        setActivatorNodeRef,
    } = useSortable({ id: slide.id || `${slide.index}` });

    const style = {
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.5 : 1,
    };

    const handleClick = () => {
        const now = Date.now();

        // Debounce clicks - only allow one click every 300ms
        if (now - lastClickTime.current < 300) {
            return;
        }

        // Only trigger click if not dragging
        if (!isDragging) {
            lastClickTime.current = now;
            onSlideClick(slide.index);
        }
    };

    return (
        <SlideThumbnailCard
            ref={setNodeRef}
            slide={slide}
            index={index}
            selected={selectedSlide === index}
            previewScale={previewScale}
            style={style}
            onPointerDown={listeners?.onPointerDown as PointerEventHandler<HTMLDivElement> | undefined}
            dragHandleProps={{
                ...attributes,
                ...listeners,
                onPointerDown: undefined,
                ref: setActivatorNodeRef,
                "aria-label": t('reorder', { index: index + 1 }),
            }}
            onClick={handleClick}
        />
    );
}
