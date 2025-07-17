import React from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import Wrapper from "@/components/Wrapper";

const EmptyStateView: React.FC = () => {
    const router = useRouter();

    return (
        <Wrapper>
            <div className="max-w-[1000px] min-h-screen flex justify-center items-center mx-auto px-4 sm:px-6 pb-6">
                <div className="mt-4 sm:mt-8 font-instrument_sans text-center">
                    <h4 className="text-lg sm:text-xl font-medium mb-4">
                        No Presentation ID Found
                    </h4>
                    <p className="text-gray-600 mb-4">Please start a new presentation.</p>
                    <Button
                        onClick={() => router.push("/upload")}
                        className="bg-[#5146E5] w-full rounded-xl text-base sm:text-lg py-4 sm:py-6 font-roboto font-semibold hover:bg-[#5146E5]/80 text-white mt-4 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        Start New Presentation
                    </Button>
                </div>
            </div>
        </Wrapper>
    );
};

export default EmptyStateView; 