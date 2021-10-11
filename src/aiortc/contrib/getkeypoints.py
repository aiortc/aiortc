"""
The following module extracts 68 tuples of facial keypoints. 
"""

# Importing libraries
import torch
from torchvision import transforms
from PIL import Image
import pathlib
import numpy as np
import cv2
import face_alignment
from torchvision.utils import save_image

class KeypointsGenerator():
    def __init__(self):        
        self.to_tensor = transforms.ToTensor()
        self.fa = face_alignment.FaceAlignment(face_alignment.LandmarksType._2D, \
                                               flip_input=True, device='cpu')

    def get_keypoints(self, input_frames, image_size = 256, crop_data = False):
        """Generates dataset images, keypoints (also called poses),
         and segmenatations from input_frames/ frames

        Inputs
        ----------
        input_frames: list of images
        crop_data : A flag used center-crop output images and poses
        (the original paper used crop_data=True,
        so for consistency we use crop_data=True as well)

        Returns
        -------
        poses:  torch tensor of keypoints 

        """        
        poses = []
        # Finding the batch-size of the input imgs
        if len(input_frames.shape) == 3:
            input_frames = input_frames[None]
            N = 1

        else:
            N = input_frames.shape[0]

        # Iterate over all the images in the batch
        for i in range(N):
            
            pose = self.fa.get_landmarks(input_frames[i])[0]
            # Finding the center of the face using the pose coordinates
            center = ((pose.min(0) + pose.max(0)) / 2).round().astype(int)

            # Finding the maximum between the width and height of the image 
            size = int(max(pose[:, 0].max() - pose[:, 0].min(), pose[:, 1].max() - pose[:, 1].min()))
            center[1] -= size // 6

            if input_frames is None:
                if crop_data:
                    # Crop poses
                    output_size = size * 2
                    pose -= center - size

            else:
                
                img = Image.fromarray(np.array(input_frames[i]))

                if crop_data:
                    # Crop images and poses
                    img = img.crop((center[0]-size, center[1]-size, center[0]+size, center[1]+size))
                    output_size = img.size[0]
                    pose -= center - size
                
                # Resizing the image before storing it.
                # If the image is small, this action would add black border around the image
                img = img.resize((image_size, image_size), Image.BICUBIC)

            # This following action (scaling the poses) is done in training pipeline,
            # and should not be done for generating the dataset.
            if crop_data:
                # This sets the range of pose to 0-256. This is what is needed for voxceleb.py 
                pose = image_size*pose / float(output_size)
            ## poses.append(torch.from_numpy((pose - 0.5) * 2).view(-1))            
            poses.append(torch.from_numpy((pose))) #poses.append(torch.from_numpy((pose)).view(-1))

        # Stack the poses from different images
        poses = torch.stack(poses, 0)[None]

        return poses.squeeze()

