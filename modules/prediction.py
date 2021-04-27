import torch
import torch.nn as nn
import torch.nn.functional as F

import math

from torch.nn.modules.sparse import Embedding
import config


class Attention(nn.Module):

    def __init__(self, input_size, hidden_size, num_classes):
        super(Attention, self).__init__()
        self.attention_cell = AttentionCell(input_size, hidden_size, num_classes)
        self.hidden_size = hidden_size
        self.num_classes = num_classes
        self.generator = nn.Linear(hidden_size, num_classes)

    def _char_to_onehot(self, input_char, onehot_dim=38):
        input_char = input_char.unsqueeze(1)
        batch_size = input_char.size(0)
        one_hot = torch.FloatTensor(batch_size, onehot_dim).zero_().to(input_char.device)
        one_hot = one_hot.scatter_(1, input_char, 1)
        return one_hot

    def forward(self, encoder_output, text, overlap, scene, is_train):
        """
        input:
            batch_H : contextual_feature H = hidden state of encoder. [batch_size x num_steps x contextual_feature_channels]
            text : the text-index of each image. [batch_size x (max_length+1)]. +1 for [GO] token. text[:, 0] = [GO].
        output: probability distribution at each step [batch_size x num_steps x num_classes]
        """
        batch_size = encoder_output.size(0)
        num_steps = config.MAX_TEXT_LENGTH + 1  # +1 for [s] at end of sentence.

        output_hiddens = torch.FloatTensor(batch_size, num_steps, self.hidden_size).fill_(0).to(encoder_output.device)
        hidden = (torch.FloatTensor(batch_size, self.hidden_size).fill_(0).to(encoder_output.device), 
        torch.FloatTensor(batch_size, self.hidden_size).fill_(0).to(encoder_output.device))
        #hidden = (overlap, overlap)
        
        if is_train:
            for i in range(num_steps):
                # one-hot vectors for a i-th char. in a batch
                char_onehots = self._char_to_onehot(text[:, i], onehot_dim=self.num_classes)
                # hidden : decoder's hidden s_{t-1}, batch_H : encoder's hidden H, char_onehots : one-hot(y_{t-1})
                #print('hidden[0]',hidden[0].shape,'hidden[1]',hidden[1].shape,' | batch_H', batch_H.shape)
                #hidden[0], hidden[1] = init_hidd, init_hidd
                hidden, alpha = self.attention_cell(hidden, encoder_output, char_onehots, overlap=overlap, scene=scene)
                output_hiddens[:, i, :] = hidden[0]  # LSTM hidden index (0: hidden, 1: Cell)
            probs = self.generator(output_hiddens)

        else:
            targets = torch.LongTensor(batch_size).fill_(0).to(encoder_output.device)  # [GO] token
            probs = torch.FloatTensor(batch_size, num_steps, self.num_classes).fill_(0).to(encoder_output.device)

            for i in range(num_steps):
                char_onehots = self._char_to_onehot(targets, onehot_dim=self.num_classes)
                hidden, alpha = self.attention_cell(hidden, encoder_output, char_onehots, overlap=overlap,scene=scene)
                probs_step = self.generator(hidden[0])
                probs[:, i, :] = probs_step
                _, next_input = probs_step.max(1)
                targets = next_input

        return probs  # batch_size x num_steps x num_classes


class AttentionCell(nn.Module):

    def __init__(self, input_size, hidden_size, num_embeddings):
        super(AttentionCell, self).__init__()
        self.i2h = nn.Linear(input_size, hidden_size, bias=False)
        self.h2h = nn.Linear(hidden_size, hidden_size)  # either i2i or h2h should have bias
        self.score = nn.Linear(hidden_size, 1, bias=False)
        self.rnn = nn.LSTMCell(input_size + num_embeddings, hidden_size)
        self.hidden_size = hidden_size

        #self.fc1 = nn.Linear(608, 352)

    def forward(self, prev_hidden, batch_H, char_onehots, overlap, scene):
        # [batch_size x num_encoder_step x num_channel] -> [batch_size x num_encoder_step x hidden_size]
        batch_H_proj = self.i2h(batch_H)
        prev_hidden_proj = self.h2h(prev_hidden[0]).unsqueeze(1)
        #overlap = overlap.unsqueeze(1)
        #print(batch_H_proj.shape, h1.shape)
        e = self.score(torch.tanh(batch_H_proj + prev_hidden_proj))  # batch_size x num_encoder_step * 1

        alpha = F.softmax(e, dim=1)
        context = torch.bmm(alpha.permute(0, 2, 1), batch_H).squeeze(1)  # batch_size x num_channel

        concat_context = torch.cat([context, char_onehots], 1)  # batch_size x (num_channel + num_embedding)
       
       # concat_context = torch.cat([concat_context], 1) 
       # concat_context = self.fc1(concat_context)
       # print(prev_hidden[0].shape)

        rnn_hidd = prev_hidden[0]
        rnn_cell = prev_hidden[1]

        cur_hidden = self.rnn(concat_context, (rnn_hidd, rnn_cell))
        #print(len(cur_hidden), cur_hidden[0].shape)
        return cur_hidden, alpha

class TF_encoder_prediction(nn.Module):
    def __init__(self, ntoken, ninp, nhid=256, nhead=2, nlayers=2, dropout=0.2):
        raise Exception('TF_encoder_prediction not implemented correctly yet, dont use!')
        super(TF_encoder_prediction, self).__init__()
        self.src_mask = None
        self.pos_encoder = PositionalEncoding(ninp, dropout)
        encoder_layers = nn.TransformerEncoderLayer(ninp, nhead, nhid, dropout)
        self.transformer_encoder = nn.TransformerEncoder(encoder_layers, nlayers)
        self.ninp = ninp
        self.decoder = nn.Linear(ninp, ntoken)

        self.init_weights()

    def _generate_square_subsequent_mask(self, sz):
        mask = (torch.triu(torch.ones(sz, sz)) == 1).transpose(0, 1)
        mask = mask.float().masked_fill(mask == 0, float('-inf')).masked_fill(mask == 1, float(0.0))
        return mask

    def init_weights(self):
        initrange = 0.1
        self.decoder.bias.data.zero_()
        self.decoder.weight.data.uniform_(-initrange, initrange)

    def forward(self, src):
        if self.src_mask is None or self.src_mask.size(0) != len(src):
            mask = self._generate_square_subsequent_mask(len(src)).to(src.device)
            self.src_mask = mask

        src = src * math.sqrt(self.ninp)
        src = self.pos_encoder(src)
        output = self.transformer_encoder(src, self.src_mask)
        output = self.decoder(output)
        return output


from utils import AttnLabelConverter
import string
converter = AttnLabelConverter(string.printable[:-6])

class TF_Decoder(nn.Module):
    def __init__(self, hidden_size, num_classes, embed_dim):
        super(TF_Decoder, self).__init__()
        self.decoder_layer = TransformerDecoderLayer(d_model=embed_dim, nhead=8, dim_feedforward=2048, dropout=0.1)
        self.layer_norm = nn.LayerNorm(embed_dim)
        self.decoder = TransformerDecoder(self.decoder_layer, num_layers=6, norm=self.layer_norm)
        self.hid_to_emb = nn.Linear(hidden_size, embed_dim)
        self.emb = nn.Embedding(num_classes, embed_dim)
        self.emb_to_classes = nn.Linear(embed_dim, num_classes)
        self.pos_encoder = PositionalEncoding(config.EMBED_DIM)

        self.num_classes = num_classes
        self.embed_dim = embed_dim

        #self.init_weights()

    def _generate_square_subsequent_mask(self, sz):
        mask = (torch.triu(torch.ones(sz, sz)) == 1).transpose(0, 1)
        mask = mask.float().masked_fill(mask == 0, float('-inf')).masked_fill(mask == 1, float(0.0))
        return mask

    def init_weights(self):
        initrange = 0.1
        self.fc.bias.data.zero_()
        self.fc.weight.data.uniform_(-initrange, initrange)

    def forward(self, encoder_output, text, overlap, scene, is_train):

        # convert memory dim to character embedding dim
        memory = self.hid_to_emb(encoder_output)
        memory = memory.permute(1,0,2)

        #overlap = overlap[:,0,:].unsqueeze(1)

        if is_train: # Training

            # convert targets from [batch, seq, feats] -> [seq, batch, feats] and apply embedding and position encoding
            targets = text[:memory.shape[1],:]
            #if str(overlap.device)[-1] == config.PRIMARY_DEVICE[-1]: print('Train targets:', targets[0])
            targets = targets.permute(1,0)
            
            
            #targets[0,:] = overlap
            emb_targets = self.emb(targets)
            emb_targets = self.pos_encoder(emb_targets)

            # generate target mask and pass to decoder
            target_mask = self._generate_square_subsequent_mask(config.MAX_TEXT_LENGTH+1).to(encoder_output.device)
            output = self.decoder(tgt=emb_targets, memory=memory, overlap=overlap, scene=scene, tgt_mask=target_mask)

            # map embeding dim to number of classes
            output = self.emb_to_classes(output)

        else: # Inference

            # Declare targets and output as zero tensors of output shape
            targets = torch.zeros(config.MAX_TEXT_LENGTH+1, memory.shape[1])
            targets = targets.to(encoder_output.device)

            output = torch.zeros(config.MAX_TEXT_LENGTH, memory.shape[1], self.num_classes).to(encoder_output.device)

            for t in range(config.MAX_TEXT_LENGTH):
                #if str(overlap.device)[-1] == config.PRIMARY_DEVICE[-1]: print('TAR:', targets[t][0].item())
                target_mask = self._generate_square_subsequent_mask(t+1).to(encoder_output.device)
                
                # convert targets into embeddings and apply positional encoding
                emb_targets = self.emb(targets.long())
                emb_targets = self.pos_encoder(emb_targets)
                #emb_targets[0,:] = overlap

                # pass embed targets and encoder memory to decoder
                t_output = self.decoder(tgt=emb_targets[:t+1], memory=memory, overlap=overlap, scene=scene, tgt_mask=target_mask)

                # map embeding dim to number of classes
                t_output = self.emb_to_classes(t_output)

                # take index class with max probability and append to targets and output sequence
                _, char_index = t_output[-1].max(1)
                targets[t+1,:] = char_index
                output[t,:] = t_output[t]

        output = output.permute(1,0,2)

        return output

# Linear layer with no bias
class Linear_Decoder(nn.Module):
    def __init__(self, num_classes):
        super(Linear_Decoder, self).__init__()
        self.linear_decoder = nn.Linear(1024, num_classes)
        self.init_weights()

    def init_weights(self):
        initrange = 0.1
        self.linear_decoder.bias.data.zero_()
        self.linear_decoder.weight.data.uniform_(-initrange, initrange)

    def forward(self, encoder_output, text, overlap, scene, is_train):
        output = self.linear_decoder(encoder_output)
        return output

class PositionalEncoding(nn.Module):

    def __init__(self, d_model, dropout=0.1, max_len=26):
        super(PositionalEncoding, self).__init__()
        self.dropout = nn.Dropout(p=dropout)

        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        pe = pe.unsqueeze(0).transpose(0, 1)
        self.register_buffer('pe', pe)

    def forward(self, x):
        x = x + self.pe[:x.size(0), :]
        return self.dropout(x)



class TransformerDecoder(nn.Module):
    __constants__ = ['norm']

    def __init__(self, decoder_layer, num_layers, norm=None):
        super(TransformerDecoder, self).__init__()
        self.layers = nn.modules.transformer._get_clones(decoder_layer, num_layers)
        self.num_layers = num_layers
        self.norm = norm

    def forward(self, tgt, memory, overlap, scene, tgt_mask= None, memory_mask = None, tgt_key_padding_mask = None, memory_key_padding_mask = None):
            output = tgt

            for mod in self.layers:
                output = mod(output, memory, overlap=overlap, scene=scene, 
                            tgt_mask=tgt_mask, memory_mask=memory_mask,
                            tgt_key_padding_mask=tgt_key_padding_mask,
                            memory_key_padding_mask=memory_key_padding_mask)

            if self.norm is not None:
                output = self.norm(output)

            return output



# TransformerDecoderLayer taken directly from PyTorch source code: https://pytorch.org/docs/stable/generated/torch.nn.TransformerDecoderLayer.html#torch.nn.TransformerDecoderLayer
class TransformerDecoderLayer(nn.Module):
    def __init__(self, d_model, nhead, dim_feedforward=2048, dropout=0.1):
        super(TransformerDecoderLayer, self).__init__()
        self.self_attn = nn.MultiheadAttention(d_model, nhead, dropout=dropout)
        self.multihead_attn = nn.MultiheadAttention(d_model, nhead, dropout=dropout)
        self.semantic_multihead_attn = nn.MultiheadAttention(d_model, nhead, dropout=dropout)
        self.linear1 = nn.Linear(d_model, dim_feedforward)
        self.dropout = nn.Dropout(dropout)
        self.linear2 = nn.Linear(dim_feedforward, d_model)


        self.semantic_to_emb = nn.Linear(512, d_model)

        self.norm1 = nn.LayerNorm(d_model)
        self.norm2 = nn.LayerNorm(d_model)
        self.norm3 = nn.LayerNorm(d_model)
        self.norm4 = nn.LayerNorm(d_model)
        self.dropout1 = nn.Dropout(dropout)
        self.dropout2 = nn.Dropout(dropout)
        self.dropout3 = nn.Dropout(dropout)
        self.dropout4 = nn.Dropout(dropout)

        self.activation = F.relu

    def __setstate__(self, state):
        if 'activation' not in state:
            state['activation'] = F.relu
        super(TransformerDecoderLayer, self).__setstate__(state)

    def forward(self, tgt, memory, overlap, scene, tgt_mask = None, memory_mask = None, tgt_key_padding_mask = None, memory_key_padding_mask = None):

        tgt2 = self.self_attn(tgt, tgt, tgt, attn_mask=tgt_mask,
                              key_padding_mask=tgt_key_padding_mask)[0]
        tgt = tgt + self.dropout1(tgt2)
        tgt = self.norm1(tgt)

        #print('prev:', tgt.shape, memory.shape)
        tgt2 = self.multihead_attn(tgt, memory, memory, attn_mask=memory_mask,
                                   key_padding_mask=memory_key_padding_mask)[0]
        tgt = tgt + self.dropout2(tgt2)
        tgt = self.norm2(tgt)
        
        # overlap = overlap.permute(1,0,2).repeat(26,1,1)
        # overlap = self.semantic_to_emb(overlap)
        # semantic_tgt = self.semantic_multihead_attn(tgt, overlap, overlap)[0]
        # tgt = tgt + self.dropout3(semantic_tgt)
        # tgt = self.norm3(tgt)

        tgt2 = self.linear2(self.dropout(self.activation(self.linear1(tgt))))
        tgt = tgt + self.dropout4(tgt2)
        tgt = self.norm4(tgt)
        return tgt