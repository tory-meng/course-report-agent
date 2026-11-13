"""在训练环境用真实tokenizer检查未批准候选；不加载模型权重。"""
import argparse
from report_agent.common import path,read_rows,save
from .data import encode_payload

def inspect(rows,tokenizer,max_length):
    results=[]
    for row in rows:
        encoded=encode_payload(tokenizer,row,max_length);labels=encoded['labels']
        start=next(i for i,v in enumerate(labels) if v!=-100)
        if labels[-1]!=tokenizer.convert_tokens_to_ids('<|im_end|>'):raise ValueError('结束标记未监督')
        if labels[start:]!=encoded['input_ids'][start:]:raise ValueError('答案监督与token不一致')
        results.append({'id':row['id'],'split':row['split'],'input_tokens':start,
                        'answer_and_end_tokens':len(labels)-start,'total_tokens':len(labels),'approval_required':True})
    return {'status':'passed','max_length':max_length,'max_tokens':max(r['total_tokens'] for r in results),
            'rows':results,'model_weights_loaded':False,'training_started':False,'truncation':False,
            'supervision':'assistant答案与im_end；输入与padding不监督；复用正式训练编码函数，未绕过训练审批。'}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--data',nargs='+',required=True)
    parser.add_argument('--tokenizer',required=True);parser.add_argument('--max-length',type=int,required=True);parser.add_argument('--out',required=True)
    a=parser.parse_args()
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained(str(path(a.tokenizer)),local_files_only=True,trust_remote_code=False)
    rows=[r for p in a.data for r in read_rows(path(p))]
    result=inspect(rows,tokenizer,a.max_length);result['tokenizer']=str(path(a.tokenizer));save(path(a.out),result)
    print({'status':result['status'],'rows':len(rows),'max_tokens':result['max_tokens'],'model_weights_loaded':False})

if __name__=='__main__':main()
