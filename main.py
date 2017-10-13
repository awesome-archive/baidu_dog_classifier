#! -*- coding:utf-8 -*-

import numpy as np
from scipy import misc
import tensorflow as tf
from keras.applications.xception import Xception,preprocess_input
from keras.layers import Input,Dense,Lambda,Embedding
from keras.layers.merge import multiply
from keras import backend as K
from keras.models import Model
from keras.optimizers import SGD
from tqdm import tqdm
import glob
np.random.seed(2017)
tf.set_random_seed(2017)

img_size = 299 #定义一些参数
nb_classes = 100
batch_size = 32
feature_size = 64

input_image = Input(shape=(img_size,img_size,3))
base_model = Xception(input_tensor=input_image, weights='imagenet', include_top=False, pooling='avg') #基础模型是Xception，加载预训练的imagenet权重，但不包括最后的全连接层

for layer in base_model.layers: #冻结Xception的所有层
    layer.trainable = False

dense = Dense(feature_size)(base_model.output)
gate = Dense(feature_size, activation='sigmoid')(base_model.output)
feature = multiply([dense,gate]) #以上三步构成了所谓的GLU激活函数
predict = Dense(nb_classes, activation='softmax', name='softmax')(feature) #分类
auxiliary = Dense(nb_classes, activation='softmax', name='auxiliary')(base_model.output) #直连边分类

input_target = Input(shape=(1,))
centers = Embedding(nb_classes, feature_size)(input_target)
l2_loss = Lambda(lambda x: K.sum(K.square(x[0]-x[1][:,0]), 1, keepdims=True), name='l2')([feature,centers]) #定义center loss

model_1 = Model(inputs=[input_image,input_target], outputs=[predict,l2_loss,auxiliary])
model_1.compile(optimizer='adam', 
                loss=['sparse_categorical_crossentropy',lambda y_true,y_pred: y_pred,'sparse_categorical_crossentropy'], 
                loss_weights=[1.,0.25,0.25], 
                metrics={'softmax':'accuracy','auxiliary':'accuracy'})
model_1.summary() #第一阶段的模型，用adam优化

for i,layer in enumerate(model_1.layers):
    if 'block13' in layer.name:
        break

for layer in model_1.layers[i:len(base_model.layers)]: #这两个循环结合，实现了放开两个block的参数
    layer.trainable = True

sgd = SGD(lr=1e-4, momentum=0.9) #定义低学习率的SGD优化器
model_2 = Model(inputs=[input_image,input_target], outputs=[predict,l2_loss,auxiliary])
model_2.compile(optimizer=sgd, 
                loss=['sparse_categorical_crossentropy',lambda y_true,y_pred: y_pred,'sparse_categorical_crossentropy'], 
                loss_weights=[1.,0.25,0.25], 
                metrics={'softmax':'accuracy','auxiliary':'accuracy'})
model_2.summary() #第二阶段的模型，用sgd优化

model = Model(inputs=input_image, outputs=[predict,auxiliary]) #用来预测的模型
model.compile(optimizer='adam', loss='sparse_categorical_crossentropy', metrics=['accuracy'])

import pandas as pd
train_txt = pd.read_csv('../train.txt', delimiter=' ', header=None)[[0,1]] #txt记录的是每张图片的类别
myid2typeid = dict(enumerate(train_txt[1].unique())) #txt记录的类别是有空的，需要映射为连续的
typeid2myid = {j:i for i,j in myid2typeid.items()}
train_txt[1] = train_txt[1].apply(lambda s: typeid2myid[s])
train_txt = train_txt.sample(frac=1) #打算训练数据集
train_txt.index = range(len(train_txt))
train_imgs = list(train_txt[0])

train_txt = dict(list(train_txt.groupby(1)))
train_data,valid_data = {},pd.DataFrame()
train_frac = 0.9 #划分一个验证集
for i,j in train_txt.items(): #每个类中拿出10%作为验证集
    train_data[i] = j[:int(len(j)*train_frac)]
    valid_data = valid_data.append(j[int(len(j)*train_frac):], ignore_index=True)

#定义插值方式，当初将它定义为函数，本是希望随机使用不同的插值方式，这也是一种数据扩增的方式，但后来去掉了随机性。
def interp_way():
    return 'nearest'

def random_reverse(x): #随机水平翻转，概率是0.5
    if np.random.random() > 0.5:
        x = x[:,::-1]
    return x

def random_rotate(x): #随机旋转，幅度是-10～10角度
    angle = 10
    r = (np.random.random()*2-1)*angle
    return misc.imrotate(x, r, interp=interp_way())

def Zoom(x, random=True): #缩放函数
    if random: #随机缩放
        r = np.random.random()*0.4+0.8 #随机缩放比例是0.8～1.2
        img_size_ = int(img_size*r)
        x = misc.imresize(x, (img_size_,img_size_), interp=interp_way())
        idx,idy = np.random.randint(0, np.abs(img_size_-img_size)+1, 2)
        if r >= 1.: #如果是放大，则随机截取一块
            return x[idx:idx+img_size,idy:idy+img_size]
        else: #如果是缩小，则随机读取一张训练集，然后把缩小后的图像贴上去
            x_ = misc.imresize(misc.imread('../train/%s.jpg'%np.random.choice(train_imgs)), (img_size,img_size))
            x_[idx:idx+img_size_,idy:idy+img_size_] = x
            return x_
    else: #不随机的话，直接缩放到标准尺寸
        x = misc.imresize(x, (img_size,img_size), interp=interp_way())
        return x

#下面是实现两张同类照片随机拼接的代码，通过“同类拼接仍为同类”的思想，构造更多样的样本
#共可以提出4中不同的拼接方式：两种对角线拼接、水平拼接、垂直拼接，4种方式随机选择
cross1 = np.tri(img_size,img_size)
cross2 = np.rot90(cross1)
cross1 = np.expand_dims(cross1, 2)
cross2 = np.expand_dims(cross2, 2)
def random_combine(x,y):
    r,idx = np.random.random(),np.random.randint(img_size/4, img_size*3/4)
    if r > 0.75:
        return np.vstack((x[:idx],y[idx:]))
    elif r > 0.5 :
        return np.hstack((x[:,:idx],y[:,idx:]))
    elif r > 0.25:
        return cross1*x + (1-cross1)*y
    else:
        return cross2*x + (1-cross2)*y

M1 = np.ones((img_size,img_size))
M1[:img_size/2,:img_size/2] = 0
M2 = np.expand_dims(np.rot90(M1, 1), 2)
M3 = np.expand_dims(np.rot90(M1, 2), 2)
M4 = np.expand_dims(np.rot90(M1, 3), 2)
M1 = np.expand_dims(M1, 2)
def random_mask(x, p=0.5): #随机把图片遮掩掉1/4，类似dropout的做法
    r = np.random.random()
    s = p/4
    if r > 1-s:
        return M1*x
    elif r > 1-s*2:
        return M2*x
    elif r > 1-s*3:
        return M3*x
    elif r > 1-s*4:
        return M4*x
    else:
        return x

def center_crop(x): #只截取图像中心部分，这是预测的时候用到的。
    idx = np.abs(x.shape[1]-x.shape[0])/2
    if x.shape[0] < x.shape[1]:
        return x[:, idx:x.shape[0]+idx]
    else:
        return x[idx:x.shape[1]+idx, :]

def random_imread(img_path, rotate=True): #综合上面的数据扩增函数来写的读图像函数
    img = misc.imread(img_path)
    img = center_crop(img)
    if rotate:
        img = Zoom(random_rotate(img), True)
    else:
        img = Zoom(img, True)
    return random_reverse(img).astype(float)

def just_imread(img_path): #不做数据扩增的读图像函数
    img = misc.imread(img_path)
    img = center_crop(img)
    img = Zoom(img, False).astype(float)
    return img

result_filename = '__test_result_2_2245.txt' #这是预测结果文件，迁移学习的时候用
choice_weights = np.array([1.*len(train_txt[i]) for i in range(nb_classes)])
choice_weights /= choice_weights.sum() #定义每个类的权重
def train_data_generator(stage,train_data): #训练集的generator，训练过程中用
    if stage == 'Train_DA': #训练阶段，数据扩增
        _ = {}
        for i,j in train_data.items():
            j_ = j.copy()
            j_[0] = zip(j[0].sample(frac=1),j[0].sample(frac=1))
            _[i] = j.append(j_, ignore_index=True)
        train_data = _ #以上几步预先整理好随机拼接的图片对
        while True:
            _ = np.random.choice(nb_classes, batch_size/2, False, choice_weights) #先选类
            batch = pd.DataFrame()
            for idx in _: #每个类选两个样本
                batch = batch.append(train_data[idx].sample(2), ignore_index=True)
            x,y = [],[]
            for i,(img_path,myid) in batch.iterrows():
                if len(img_path) == 2: #这是随机拼接的情形
                    img1,img2 = just_imread('../train/%s.jpg'%img_path[0]),just_imread('../train/%s.jpg'%img_path[1])
                    x.append(random_combine(img1, img2)) #随机拼接就不做其他的数据扩增了
                else:
                    img = random_mask(random_imread('../train/%s.jpg'%img_path)) #完整的随机数据扩增
                    x.append(img)
                y.append([myid])
            x,y = np.array(x),np.array(y)
            yield [preprocess_input(x),y], [y,y,y] #构成keras模型所需要的输出
    elif stage == 'Train': #训练阶段，减少数据扩增
        while True:
            _ = np.random.choice(nb_classes, batch_size/2, False, choice_weights)
            batch = pd.DataFrame()
            for idx in _:
                batch = batch.append(train_data[idx].sample(2), ignore_index=True)
            x,y = [],[]
            for i,(img_path,myid) in batch.iterrows(): #随机遮掩和随机拼接都被去除
                img = random_imread('../train/%s.jpg'%img_path, False)
                x.append(img)
                y.append([myid])
            x,y = np.array(x),np.array(y)
            yield [preprocess_input(x),y], [y,y,y]
    elif stage == 'Transfer_DA': #迁移学习阶段，代码跟训练阶段是一样的，只不过加入了测试集的预测结果
        train_data = train_txt.copy()
        test_result = pd.read_csv(result_filename, delimiter='\t', header=None)[[1,0]]
        test_result.columns = [0,1]
        test_result[1] = test_result[1].apply(lambda s: typeid2myid[s])
        for i,j in test_result.groupby(1):
            train_data[i] = train_data[i].append(j, ignore_index=True)
        _ = {}
        for i,j in train_data.items():
            j_ = j.copy()
            j_[0] = zip(j[0].sample(frac=1),j[0].sample(frac=1))
            _[i] = j.append(j_, ignore_index=True)
        train_data = _
        while True:
            _ = np.random.choice(nb_classes, batch_size/2, False, choice_weights)
            batch = pd.DataFrame()
            for idx in _:
                batch = batch.append(train_data[idx].sample(2), ignore_index=True)
            x,y = [],[]
            for i,(img_path,myid) in batch.iterrows():
                if len(img_path) == 2:
                    #人工把测试集的所有图片复制一份到train目录下
                    img1,img2 = just_imread('../train/%s.jpg'%img_path[0]),just_imread('../train/%s.jpg'%img_path[1])
                    x.append(random_combine(img1, img2))
                else:
                    img = random_mask(random_imread('../train/%s.jpg'%img_path))
                    x.append(img)
                y.append([myid])
            x,y = np.array(x),np.array(y)
            yield [preprocess_input(x),y], [y,y,y]
    elif stage == 'Transfer':
        train_data = train_txt
        test_result = pd.read_csv(result_filename, delimiter='\t', header=None)[[1,0]]
        test_result.columns = [0,1]
        test_result[1] = test_result[1].apply(lambda s: typeid2myid[s])
        for i,j in test_result.groupby(1):
            train_data[i] = train_data[i].append(j, ignore_index=True)
        while True:
            _ = np.random.choice(nb_classes, batch_size/2, False, choice_weights)
            batch = pd.DataFrame()
            for idx in _:
                batch = batch.append(train_data[idx].sample(2), ignore_index=True)
            x,y = [],[]
            for i,(img_path,myid) in batch.iterrows():
                img = random_imread('../train/%s.jpg'%img_path, False)
                x.append(img)
                y.append([myid])
            x,y = np.array(x),np.array(y)
            yield [preprocess_input(x),y], [y,y,y]

def valid_data_generator(): #验证集的generator，训练过程中用
    x,y = [],[]
    for i,(img_path,myid) in valid_data.iterrows():
        img = just_imread('../train/%s.jpg'%img_path)
        x.append(img)
        y.append(myid)
        if len(x) == batch_size:
            yield preprocess_input(np.array(x)), np.array(y)
            x,y = [],[]
    if x:
        yield preprocess_input(np.array(x)), np.array(y)

test_imgs = glob.glob('../test/*.jpg')
test_imgs = [i.replace('../test/','').replace('.jpg','') for i in test_imgs]
def test_data_generator(): #测试集的generator，生成结果文件用
    x = []
    for img_path in test_imgs:
        img = just_imread('../test/%s.jpg'%img_path)
        x.append(img)
        if len(x) == batch_size:
            yield preprocess_input(np.array(x))
            x = []
    if x:
        yield preprocess_input(np.array(x))


if __name__ == '__main__':

    #训练过程
    train_epochs = 30
    alpha = 0.5
    for i in range(train_epochs):
        print 'train epoch %s working ...'%i
        if i < 10: #第一阶段训练
            model_1.fit_generator(train_data_generator('Train_DA',train_data), steps_per_epoch=200, epochs=3)
            if i < 9:
                continue
        elif i < 20: #第二阶段训练
            model_2.fit_generator(train_data_generator('Train_DA',train_data), steps_per_epoch=200, epochs=3)
        else: #第三阶段训练
            model_2.fit_generator(train_data_generator('Train',train_data), steps_per_epoch=200, epochs=3)
        valid_x_0 = []
        valid_x_1 = []
        valid_y = []
        for x,y in tqdm(valid_data_generator()):
            _ = model.predict(x)
            valid_x_0.append(_[0])
            valid_x_1.append(_[1])
            valid_y.append(y)
        valid_x = np.vstack(valid_x_0),np.vstack(valid_x_1)
        valid_y = np.hstack(valid_y)
        total = 1.*len(valid_x[0])
        right_0 = (valid_y == valid_x[0].argmax(axis=1)).sum()
        right_1 = (valid_y == valid_x[1].argmax(axis=1)).sum()
        acc_0 = right_0/total
        acc_1 = right_1/total
        right_2 = [(valid_y == ((h/100.)*valid_x[0]+(1-h/100.)*valid_x[1]).argmax(axis=1)).sum() for h in range(101)] #枚举搜索最佳权重
        acc_2 = np.max(right_2)/total
        alpha = np.argmax(right_2)/100.
        print 'epoch %s, acc_0 %s, acc_1 %s, acc_2 %s'%(i, acc_0, acc_1, acc_2)
        model_1.save_weights('main_%s_%s_%s_%s_%s.model'%(i, int(acc_0*10000), int(acc_1*10000), int(acc_2*10000), int(alpha*10000)))

    #迁移过程，迁移过程不能和训练过程同时跑，一般是上面的训练过程跑完了，然后设置上面的train_epochs=0，更改下面的train_epochs=30，然后重新运行脚本
    train_epochs = 30*0
    if train_epochs > 0:
        model_1.load_weights('__main_29_8165_8075_8165_10000_2289.model')
    for i in range(train_epochs):
        print 'transfer epoch %s working ...'%i
        if i < 10:
            model_2.fit_generator(train_data_generator('Transfer_DA',train_data), steps_per_epoch=200, epochs=3)
        elif i < 20:
            model_2.fit_generator(train_data_generator('Transfer',train_data), steps_per_epoch=200, epochs=3)
        else:
            model_1_.fit_generator(train_data_generator('Train',train_data), steps_per_epoch=200, epochs=3)
        model_1.save_weights('main_t1_%s.model'%i)

    #测试过程，生成三份预测结果，可以分别提交比较哪个更准确
    test_result_0 = []
    test_result_1 = []
    test_result_2 = []
    for x in tqdm(test_data_generator()):
        _ = model.predict(x)
        test_result_0.extend([myid2typeid[i] for i in _[0].argmax(axis=1)])
        test_result_1.extend([myid2typeid[i] for i in _[1].argmax(axis=1)])
        test_result_2.extend([myid2typeid[i] for i in (alpha*_[0]+(1-alpha)*_[1]).argmax(axis=1)])

    test_result_0 = pd.DataFrame(test_result_0)
    test_result_0[1] = test_imgs
    test_result_0.to_csv('test_result_0.txt', index=None, header=None, sep='\t')

    test_result_1 = pd.DataFrame(test_result_1)
    test_result_1[1] = test_imgs
    test_result_1.to_csv('test_result_1.txt', index=None, header=None, sep='\t')

    test_result_2 = pd.DataFrame(test_result_2)
    test_result_2[1] = test_imgs
    test_result_2.to_csv('test_result_2.txt', index=None, header=None, sep='\t')
