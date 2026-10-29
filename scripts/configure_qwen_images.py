"""Explicitly register the verified 231 ComfyUI image endpoint in Prism MongoDB."""
import argparse
import os
from dotenv import load_dotenv
from lumenx_prism.history import MongoHistory
from lumenx_prism.model_configs import ModelConfigs,ModelConfigInput


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--env-file',default='.env')
    parser.add_argument('--base-url',default='http://192.168.8.231:8190')
    parser.add_argument('--resource-group',default='231-gpu0')
    parser.add_argument('--bind',action='store_true',help='Explicitly replace an existing image binding')
    args=parser.parse_args();load_dotenv(args.env_file)
    repo=MongoHistory(os.environ['PRISM_MONGODB_URI'],os.getenv('PRISM_MONGODB_DATABASE','lumenx_prism'))
    try:
        configs=ModelConfigs(repo.client['lumenx_prism'])
        row=configs.collection.find_one({'record_type':'model','provider':'comfyui','model':'qwen-image-2.1','base_url':args.base_url.rstrip('/')})
        if row:config_id=row['_id']
        else:
            row=configs.save(ModelConfigInput(name='231 Qwen-Image 2.1',kind='image',provider='comfyui',
                protocol='custom_http',model='qwen-image-2.1',base_url=args.base_url,
                capabilities=['image_generation','reference_images','character_reference_card'],
                parameters={'resource_group':args.resource_group,'peer_base_urls':['http://192.168.8.231:8188'],'steps':20},
                notes='Local ComfyUI 0.39.0; fixed qwen-image21-v1 workflow.'))
            config_id=row['id']
        binding=configs.collection.find_one({'_id':'bindings'}) or {}
        if args.bind or not binding.get('image_generation_id'):
            configs.bind_image_model(config_id)
        print('Image configuration ID:',config_id)
        print('Existing text model configuration and binding preserved.')
    finally:repo.close()


if __name__=='__main__':main()
