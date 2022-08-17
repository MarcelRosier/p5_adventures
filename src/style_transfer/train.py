from PIL import Image
from io import BytesIO
import matplotlib.pyplot as plt
import matplotlib.image
import numpy as np
import torch
import torch.optim as optim
import requests
from torchvision import transforms, models
from torch.utils.tensorboard import SummaryWriter
from datetime import datetime


def load_image(img_path, max_size=400, shape=None):
    ''' Load in and transform an image, making sure the image
       is <= 400 pixels in the x-y dims.'''
    if "http" in img_path:
        response = requests.get(img_path)
        image = Image.open(BytesIO(response.content)).convert('RGB')
    else:
        image = Image.open(img_path).convert('RGB')
    
    # large images will slow down processing
    if max(image.size) > max_size:
        size = max_size
    else:
        size = max(image.size)
    
    if shape is not None:
        size = shape
        
    in_transform = transforms.Compose([
                        transforms.Resize(size),
                        transforms.ToTensor(),
                        transforms.Normalize((0.485, 0.456, 0.406), 
                                             (0.229, 0.224, 0.225))])

    # discard the transparent, alpha channel (that's the :3) and add the batch dimension
    image = in_transform(image)[:3,:,:].unsqueeze(0)
    
    return image
# use the convolutional and pooling layers to get the "features"
# portion of VGG19
vgg = models.vgg19(weights=models.VGG19_Weights.IMAGENET1K_V1).features
# freeze all VGG parameters as we're only optimizing the target
# image
for param in vgg.parameters():
    param.requires_grad_(False)

# move the model to GPU, if available (but since I'm using colab
# it doesn't really matter
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
vgg.to(device)
# print (vgg)

# load in content and style image
content = load_image('images/pisa.jpeg').to(device)
# Resize style to match content, makes code easier
style = load_image('images/ship_at_sea.jpeg', shape=content.shape[-2:]).to(device)

# helper function for un-normalizing an image 
# and converting it from a Tensor image to a NumPy image for display
def im_convert(tensor):
    """ Display a tensor as an image. """
    
    image = tensor.to("cpu").clone().detach()
    image = image.numpy().squeeze()
    image = image.transpose(1,2,0)
    image = image * np.array((0.229, 0.224, 0.225)) + np.array((0.485, 0.456, 0.406))
    image = image.clip(0, 1)
    return image


writer = SummaryWriter(f"logs/{datetime.now()}")
# # display the images
# fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(20, 10))
# # content and style ims side-by-side
# ax1.imshow(im_convert(content))
# ax2.imshow(im_convert(style))

# writer.add_figure(tag="images",figure=plt.gcf())
# writer.add_scalar("test",1,1)
# writer.flush()

def get_features(image, model, layers=None):
    """ Run an image forward through a model and get the features for 
        a set of layers. Default layers are for VGGNet matching Gatys et al (2016)
    """
    if layers is None:
        layers = {'0': 'conv1_1',
                  '5': 'conv2_1', 
                  '10': 'conv3_1', 
                  '19': 'conv4_1',
                  '21': 'conv4_2',  
                  '28': 'conv5_1'}
        
    features = {}
    x = image
    # model._modules is a dictionary holding each module in the model
    for name, layer in model._modules.items():
        x = layer(x)
        if name in layers:
            features[layers[name]] = x
            
    return features

def gram_matrix(tensor):
    ## get the batch_size, depth, height, and width of the Tensor
    _, d, h, w = tensor.size()
    
    # reshape so we're multiplying the features for each channel
    tensor = tensor.view(d, h * w)
    # calculate the gram matrix
    gram = torch.mm(tensor, tensor.t())
    return gram

# get content and style features only once before forming the target # image
content_features = get_features(content, vgg)
style_features = get_features(style, vgg)
# calculate the gram matrices for each layer of our style 
# representation
style_grams = {layer: gram_matrix(style_features[layer]) for layer in style_features}
# create a third "target" image and prep it for change
# it is a good idea to start off with the target as a copy of our 
# *content* image then iteratively change its style
target = content.clone().requires_grad_(True).to(device)

# weights for each style layer
# weighting earlier layers more will result in *larger* style 
# features
style_weights = {'conv1_1': 1.,
                 'conv2_1': 0.8,
                 'conv3_1': 0.5,
                 'conv4_1': 0.3,
                 'conv5_1': 0.1}
content_weight = 1  
style_weight = 1e6  


#train loop
# for displaying the target image, intermittently
show_every = 200

# iteration hyperparameters
optimizer = optim.Adam([target], lr=0.003)
steps = 2001  # decide how many iterations to update your image (5000)

for ii in range(0, steps+1):
    
    # get the features from your target image
    target_features = get_features(target, vgg)
    
    # the content loss
    content_loss = torch.mean((target_features['conv4_2'] - content_features['conv4_2'])**2)
    
    # the style loss
    # initialize the style loss to 0
    style_loss = 0
    # then add to it for each layer's gram matrix loss
    for layer in style_weights:
        # get the "target" style representation for the layer
        target_feature = target_features[layer]
        target_gram = gram_matrix(target_feature)
        _, d, h, w = target_feature.shape
        # get the "style" style representation
        style_gram = style_grams[layer]
        # the style loss for one layer, weighted appropriately
        layer_style_loss = style_weights[layer] * torch.mean((target_gram - style_gram)**2)
        # add to the style loss
        style_loss += layer_style_loss / (d * h * w)
        
    # calculate the *total* loss
    total_loss = content_weight * content_loss + style_weight * style_loss
    writer.add_scalar("total_loss", total_loss,ii)
    # update your target image
    optimizer.zero_grad()
    total_loss.backward()
    optimizer.step()
    
    # display intermediate images and print the loss
    if  ii % show_every == 0:
        print('Total loss: ', total_loss.item())
        plt.imshow(im_convert(target))
        writer.add_figure("figs", figure=plt.gcf(),global_step=ii)

matplotlib.image.imsave("target.png",im_convert(target))