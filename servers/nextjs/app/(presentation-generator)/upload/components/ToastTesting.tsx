import React from 'react'
import { Button } from '@/components/ui/button'
import { toast } from 'sonner'

const ToastTesting = () => {
    return (
        <div className="p-8 space-y-4">
            <h2 className="text-2xl font-bold mb-6">Toast Testing - All Variants</h2>

            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
                {/* Success Toast */}
                <Button
                    onClick={() => toast.success('Success! Operation completed successfully', {
                        description: 'Your data has been saved.',
                        duration: 4000,
                        richColors: true,
                    })}
                    className="bg-green-600 hover:bg-green-700"
                >
                    Success Toast
                </Button>

                {/* Error Toast */}
                <Button
                    onClick={() => toast.error('Error! Something went wrong', {
                        description: 'Please try again later.',
                        duration: 5000,
                        richColors: true,
                    })}
                    className="bg-red-600 hover:bg-red-700"
                >
                    Error Toast
                </Button>

                {/* Info Toast */}
                <Button
                    onClick={() => toast.info('Information', {
                        description: 'Here is some useful information for you.',
                        duration: 4000,
                        richColors: true,
                    })}
                    className="bg-blue-600 hover:bg-blue-700"
                >
                    Info Toast
                </Button>

                {/* Warning Toast */}
                <Button
                    onClick={() => toast.warning('Warning! Please be careful', {
                        description: 'This action cannot be undone.',
                        duration: 4000,
                        richColors: true,
                    })}
                    className="bg-yellow-600 hover:bg-yellow-700"
                >
                    Warning Toast
                </Button>

                {/* Loading Toast */}
                <Button
                    onClick={() => {
                        const loadingToast = toast.loading('Processing...', {
                            description: 'Please wait while we process your request.',
                        });

                        // Simulate loading completion after 3 seconds
                        setTimeout(() => {
                            toast.dismiss(loadingToast);
                            toast.success('Processing completed!');
                        }, 3000);
                    }}
                    className="bg-indigo-600 hover:bg-indigo-700"
                >
                    Loading Toast
                </Button>



                {/* Promise Toast */}
                <Button
                    onClick={() => {
                        const promise = new Promise((resolve, reject) => {
                            setTimeout(() => {
                                Math.random() > 0.5 ? resolve('Success!') : reject('Failed!');
                            }, 2000);
                        });

                        toast.promise(promise, {
                            loading: 'Uploading file...',
                            success: 'File uploaded successfully!',
                            error: 'Failed to upload file',
                        });
                    }}
                    className="bg-teal-600 hover:bg-teal-700"
                >
                    Promise Toast
                </Button>










            </div>


        </div>
    )
}

export default ToastTesting
